# oengus functionality for loading schedule info using their API.

import datetime
import logging
import re
import csv

import requests
from django.db.models import Min
from django.utils import dateparse

from tracker.models import SpeedRun, Talent #Runner
from tracker.models.event import TimestampField

SCHEDULES_URL = 'https://oengus.io/api/v2/marathons/{event_id}/schedules'
SINGLE_SCHEDULE_URL = 'https://oengus.io/api/v2/marathons/{event_id}/schedules/for-slug/{schedule_slug}'

# Ignore games with this text in the name, i.e. setup blocks, preshow, finale.
IGNORE_LIST = (
    'setup',
    'preshow',
    'pre-show',
    'finale',
)

logger = logging.getLogger(__name__)


class OengusError(Exception):
    pass


def _get_data(url):
    r = requests.get(url)

    if r.status_code != 200:
        logger.error("Error getting URL {0!r} - {1}".format(url, r.status_code))
        raise OengusError(r.status_code)

    data = r.json()
    if data.get('status'):
        logger.error("Error getting URL {0!r} - {1}: {2}".format(url, data.get('status'), data.get('message')))
        raise OengusError(data.get('status'))

    return data

def get_schedule_data(event_id):
    """Get Oengus schedule data for an event.
    Gets the first schedule if multiple are present

    :param event_id: Event slug.
    :type event_id: str
    :return: Schedule data CSV
    :rtype: list[dict]
    """
    schedules = _get_data(SCHEDULES_URL.format(event_id=event_id))
    if len(schedules) == 0:
        logger.error(f"event {event_id} has no public schedules!")
        raise OengusError("event has no public schedules")
    elif len(schedules) > 1:
        logger.warn(f"event {event_id} has multiple schedules, using the first one")
    schedule_slug = schedules["data"][0]["slug"]
    return _get_data(SINGLE_SCHEDULE_URL.format(event_id=event_id, schedule_slug=schedule_slug))['lines']


def merge_event_schedule(event):
    """Merge schedule from oengus API with an event in our system.

    :param event: Event record to merge.
    :type event: tracker.models.Event
    :return: Number of runs updated.
    :rtype: int
    """
    i = TimestampField.time_string_to_int
    num_runs = 0

    if not event.oengus_id:
        raise OengusError("Event ID not set")

    # Get schedule data.
    schedule = get_schedule_data(event.oengus_id)

    # Get existing runs in a single query.  Clear position for all for re-ordering.
    qs = SpeedRun.objects.select_for_update().filter(event=event)
    qs.update(order=None)
    existing_runs = dict([(r.name, r) for r in qs])

    # Track seen games to make sure there aren't any duplicate games on the schedule.
    games_seen = set()
    order = 0

    # Import each run from the Oengus schedules.
    for item in schedule:
        game = item["game"].strip()
        category = item["category"].strip()
        logger.warn(f"game: {game}, category: {category}")
        order += 1

        # Raise error if we have duplicate games in the schedule.
        # Skip any games with "setup" in the name, i.e. setup blocks.
        unique_name = game.lower()

        ignore = False
        for iname in IGNORE_LIST:
            if re.search(r'\b{}\b'.format(iname), unique_name):
                ignore = True
                break

        # empty game and category is how oengus marks setup blocks
        if ignore or item['setupBlock']:
            logger.debug("Skipping setup item {!r}".format(item))
            continue

        if unique_name in games_seen:
            raise OengusError("Schedule has duplicate game entry: {!r}".format(game))

        games_seen.add(unique_name)

        # Parse runners.
        runners = []
        for r in item['runners']:
            # try to find twitch link
            stream_url = next((x['username'] for x in r['profile']['connections'] if x['platform'] == 'TWITCH'), '')

            runners.append((r['runnerName'], stream_url))


        runner_names = set(r[0].lower() for r in runners)

        # Check for existing run, or make a new one.
        logger.debug("Merging run: Game {0!r}, category {1!r}, runners {2!r}".format(game, category, runners))

        if game in existing_runs:
            run = existing_runs[game]
        else:
            run = SpeedRun(event=event, name=game)

        run.category = category
        run.save()
        # run.commentators.set(commentators)
        run.order = order
        run.setup_time = str(dateparse.parse_duration(item["setupTime"]))
        run.run_time = str(dateparse.parse_duration(item["estimate"]))
        run.starttime = dateparse.parse_datetime(item["date"])
        run.endtime = run.starttime + datetime.timedelta(milliseconds=i(run.run_time) + i(run.setup_time))
        # Use times from the Oengus schedule.
        run.save(fix_time=False)

        # Make runner records.
        for u in run.runners.all():
            if u.name.lower() not in runner_names:
                run.runners.remove(u)

        current_runners = run.runners.all()
        for runner, url in runners:
            try:
                u = Talent.objects.select_for_update().filter(name__iexact=runner).get()
            except Talent.DoesNotExist:
                u = Talent()

            u.name = runner
            if url:
                u.stream = url
            u.save()

            if u not in current_runners:
                run.runners.add(u)

        # Save the run again to update the runners field.
        run.save(fix_time=False)

        # Increment counter.
        num_runs += 1

    # Set event start date based on first run.
    qs = SpeedRun.objects.filter(event=event).aggregate(start_date=Min('starttime'))
    event.datetime = qs['start_date']
    event.save()

    return num_runs
