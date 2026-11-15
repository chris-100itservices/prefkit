"""Print the compute devices prefkit can see on this machine."""

from django.core.management.base import BaseCommand

from prefkit.compute.devices import available_devices


class Command(BaseCommand):
    help = "List usable compute devices (CUDA GPU, Apple MPS, CPU)."

    def handle(self, *args, **options) -> None:
        for device in available_devices():
            memory = f"{device.memory_gb} GB" if device.memory_gb is not None else "shared/unknown"
            self.stdout.write(f"{device.kind:5} {device.name}  memory: {memory}")
