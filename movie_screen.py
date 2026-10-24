#!/usr/bin/env python3
"""1959 — the movie screen (the 27").

The attract card until a visitor puts the headphones on and presses the
mouse, then the story film, once, fully, with its sound. While the film
plays the installation app's sound is muted; when the film ends the card
comes back and the app's sound returns.

The card is this window (tkinter, drawing video/titles/card-4.png — the
film's own ending typography). The film is ffplay, the same player the
venue has always used. The mute is pactl, the same call start.sh makes.

museum.sh runs this under a supervisor loop: if it dies it comes back on
the card, and the unmute on startup means a crash mid-film cannot leave
the room silent.

Geometry follows museum.sh (DP-2-1 is x=1920 on this machine):
  VIDEO_X    the movie screen's left edge
  HIDDEN_XY  where the cursor sits during the film: the laptop's own
             dark screen, so no arrow is ever visible on the piece
  CARD_XY    where the cursor is parked for the next press
"""

import os
import subprocess
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.realpath(__file__))
CARD = os.path.join(HERE, "video", "titles", "card-4.png")
MOVIE = os.path.join(HERE, "video", "1959_master_1080p.mp4")

VIDEO_X = 1920
HIDDEN_XY = (1910, 10)
CARD_XY = (VIDEO_X + 960, 540)

state = "card"
movie_proc = None


def run(cmd):
    """Run a helper, and never die if it is missing (bench machines have
    no pactl or xdotool; the venue has both)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as e:
        print("%s failed: %s" % (" ".join(cmd), e), file=sys.stderr)
        return None


def set_app_mute(mute):
    """Mute/unmute the app's PulseAudio streams, found the same way
    start.sh finds them: the long `pactl list`, matching the stream whose
    application.name contains ofxLPM-1959."""
    r = run(["pactl", "list", "sink-inputs"])
    if r is None or r.returncode != 0:
        return
    sid = None
    for line in r.stdout.splitlines():
        if line.startswith("Sink Input #"):
            sid = line.split("#", 1)[1].strip()
        elif sid is not None and "application.name" in line \
                and "ofxLPM-1959" in line:
            run(["pactl", "set-sink-input-mute", sid,
                 "1" if mute else "0"])
    print("the app is %s" % ("muted" if mute else "unmuted"), flush=True)


def focus_app():
    """Keyboard focus belongs to the app (cal / quit keys land there)."""
    run(["xdotool", "search", "--name", "^1959$", "windowactivate"])


def park_cursor(xy):
    run(["xdotool", "mousemove", str(xy[0]), str(xy[1])])


def start_movie():
    global state, movie_proc
    if state != "card":
        return
    state = "movie"
    print("the film plays", flush=True)
    set_app_mute(True)
    root.withdraw()
    focus_app()
    park_cursor(HIDDEN_XY)
    movie_proc = subprocess.Popen(
        ["ffplay", "-autoexit", "-hide_banner", "-loglevel", "quiet",
         "-left", str(VIDEO_X), "-top", "0",
         "-x", "1920", "-y", "1080", MOVIE])
    root.after(500, poll_movie)


def poll_movie():
    if movie_proc is not None and movie_proc.poll() is None:
        root.after(500, poll_movie)
    else:
        show_card()


def show_card():
    global state, movie_proc
    state = "card"
    movie_proc = None
    print("back on the card", flush=True)
    set_app_mute(False)
    root.deiconify()
    root.lift()
    focus_app()
    park_cursor(CARD_XY)


root = tk.Tk()
root.title("1959 movie screen")
root.configure(bg="black")
photo = tk.PhotoImage(file=CARD)
tk.Label(root, image=photo, bg="black").pack(fill="both", expand=True)
root.geometry("1920x1080+%d+0" % VIDEO_X)
root.attributes("-fullscreen", True)
root.config(cursor="none")

for button in ("<Button-1>", "<Button-2>", "<Button-3>",
               "<Button-4>", "<Button-5>"):
    root.bind(button, lambda e: start_movie())

# A restart after a crash must not leave the app muted, and the cursor
# belongs on the card, where the next press lands.
set_app_mute(False)
park_cursor(CARD_XY)
root.mainloop()
