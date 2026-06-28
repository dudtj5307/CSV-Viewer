import subprocess

commands = [
    "pyuic6 -o dialog_viewer.py dialog_viewer.ui",
    "pyuic6 -o dialog_filter.py dialog_filter.ui",
    "pyuic6 -o widget_esc.py widget_esc.ui",
    "pyuic6 -o widget_graph.py widget_graph.ui",

]

for cmd in commands:
    subprocess.run(cmd, shell=True, check=True)

print("Generation complete!")
