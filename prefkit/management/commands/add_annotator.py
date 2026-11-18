"""Create an account. A labeller can label rows; an administrator (--staff) can also import."""

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

MIN_PASSWORD_CHARS = 8


class Command(BaseCommand):
    help = "Create an account for a person who labels rows. Asks for the password twice."

    def add_arguments(self, parser):
        parser.add_argument(
            "username", help="The name the person signs in with. Their labels are stored under it."
        )
        parser.add_argument(
            "--staff",
            action="store_true",
            help="Make an administrator, who can also start and cancel imports.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        name = options["username"].strip()
        if not name:
            raise CommandError("the username cannot be empty")
        if user_model.objects.filter(username=name).exists():
            raise CommandError(f"an account named '{name}' already exists")

        # getpass does not show the password while it is typed.
        password = getpass.getpass("Password: ")
        if getpass.getpass("Password again: ") != password:
            raise CommandError("the two passwords are different, so no account was created")
        if len(password) < MIN_PASSWORD_CHARS:
            raise CommandError(f"use at least {MIN_PASSWORD_CHARS} characters, so no account was created")

        user_model.objects.create_user(username=name, password=password, is_staff=options["staff"])
        role = "an administrator, who can import and label" if options["staff"] else "a labeller"
        self.stdout.write(f"created '{name}' as {role}. They can now sign in.")
