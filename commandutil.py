from django.core.management.base import BaseCommand


class TrackerCommand(BaseCommand):
    requires_system_checks = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbosity = 0

    def message(self, message, verbosity_level=1):
        if self.verbosity >= verbosity_level:
            print(message)

    def handle(self, *args, **options):
        if 'verbosity' in options:
            self.verbosity = options['verbosity']
        else:
            self.verbosity = 1
        self.message("Positional arguments: {0}".format(args), 3)
        self.message("Named arguments: {0}".format(options), 3)
