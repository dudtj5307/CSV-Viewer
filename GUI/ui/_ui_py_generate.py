import subprocess

commands = [
    "pyuic6 -o dialog_main.py dialog_main.ui",
    "pyuic6 -o dialog_settings.py dialog_settings.ui",
    "pyuic6 -o dialog_progress.py dialog_progress.ui",
    "pyuic6 -o dialog_viewer.py dialog_viewer.ui",
    "pyuic6 -o dialog_filter.py dialog_filter.ui",
    "pyuic6 -o widget_esc.py widget_esc.ui",
]

for cmd in commands:
    subprocess.run(cmd, shell=True, check=True)

print("Generation complete!")
