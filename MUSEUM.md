# `1959` at the museum — the two-screen setup

One laptop, two DP monitors, one Pi, one die. One script starts it, and the
two failures that matter heal by themselves. Nothing autostarts — see
[unattended](#unattended) for what to do after a power cut.

```
      wifi 192.168.100.0/24 (admin only)     wired 10.42.0.0/24 (the piece)
  ┌───────────────────────────────┐
  │ laptop  192.168.100.215 (DHCP)│──enp0s25──► Pi 10.42.0.189
  │ federico-HP-EliteBook-        │              FT4232 ──► the die
  │ Folio-1040-G2                 │              Pixhawk ─► the membrane pin
  └───────────────────────────────┘
    eDP-1   x=0      the laptop screen: dark, cursor parked on it
    DP-2-1  x=1920   the 27": the story film, muted, looping
    DP-2-2  x=3840   the 24": the app — spiral + film, fullscreen
```

Both monitors hang off one dock connector, so xrandr names them DP-2-1 and
DP-2-2. They are still two independent outputs; the scripts address them by
x position.

## The screens

| monitor | x | shows | sound |
|---|---|---|---|
| DP-2-1 (27") | 1920 | `video/1959_loop.mp4` — ffplay, `-an`, infinite loop, subtitles burned in | none |
| DP-2-2 (24") | 3840 | the app, fullscreen | yes |

The app's window is placed by the script because OF fullscreens a window on
the monitor it is sitting on: `museum.sh` moves it to `APP_X` first, then
asks the window manager for fullscreen. That is why `bin/data/settings.json`
on the laptop has `"fullscreen": false` — placement belongs to the script,
not the app.

## The audio

Only the app makes sound. It routes through the ALSA `default` device —
the system mixer, PulseAudio — so it lands on the default sink (the laptop's
analog jack) and can be moved live with the **O** key
(jack → bluetooth → hdmi, `switch_audio_output.sh`). Left ear is the tape,
right ear the die. The film player is muted; the subtitles carry it.

`start.sh` holds the app's stream at 100%: the mixer's stream-restore keeps
re-applying a stale 0% to every new stream of this app (RtAudio probing makes
a few short-lived ones), so it watches for the whole run and sets each new
stream once.

## Starting it

```bash
./museum.sh        # on the laptop, from ~/tue/ofxLPM-1959
```

In order: kill leftovers (`pkill -x ofxLPM-1959 ffplay`), turn off blanking
and lock, start `start.sh` (the app + the volume watcher), place the app on
DP-2-2 and fullscreen it, start the film on DP-2-1 and fullscreen it, give
the app focus (so `cal` / `quit` land there), park the cursor on the laptop
screen. `APP_X` / `VIDEO_X` at the top trade the two monitors.

Stopping: focus the app, type `quit`, then `pkill -x ffplay`.

## The chip, over the wire

`settings.json` on the laptop points at `10.42.0.189:9100:9101:9102` — the
Pi's `chip1959` systemd service (see the main README for its side). The link
is the ethernet cable; wifi is only for the admin SSH. The app redials on its
own and re-stages the whole bring-up when the link returns, and the service
reflashes the die on replug.

## Unattended

What heals by itself:

- die or scope unplugged → the service stops, the sentinel holds still, and
  on replug everything re-stages (~25 s).
- laptop wifi down → nothing. The piece never needed it.

What does not:

- **Power.** Nothing autostarts. If the laptop reboots, someone logs in and
  runs `./museum.sh`. The Pi comes up on its own (udev coldplug).
- **The lid, off AC only.** `museum.sh` sets `lid-close-ac-action nothing`
  and `sleep-inactive-ac-type nothing`, so on mains the lid can be closed.
  On battery GNOME still suspends — keep it plugged in.
- Blank screen / lock screen are off while `museum.sh` runs; they come back
  at the next login.

## The film

Since 2026-10-23 there is no white circuit schematic anywhere in the cut:
beat 3 is the die micrograph into the soma animation (the schematic's 10 s
went to the animation the narration was describing), beat 4 holds the silicon
layout for the whole beat, beat 5 goes straight to the array animation. Beat
durations and narration are untouched (153 s master / 147 s loop), so the
subtitles still line up. The pre-rework films are kept locally as
`video/1959_*_schematics.mp4`, the old beat segments as
`video/build/beat{3,4,5}_*.mp4`.

To change the film at the venue: `make -C video loop` on the development
machine, copy `video/1959_loop.mp4` to the laptop, then either rerun
`./museum.sh` (simplest — the app restarts with it, ~30 s), or swap only the
player and place it by hand:

```bash
pkill -x ffplay; sleep 1
cd ~/tue/ofxLPM-1959
setsid ffplay -an -loop 0 -hide_banner -loglevel quiet video/1959_loop.mp4 &
FWID=$(xdotool search --sync --class ffplay | head -1)
xdotool windowactivate --sync "$FWID"; sleep 1
xdotool windowmove "$FWID" 1920 0 && xdotool windowsize "$FWID" 1920 1080
sleep 2; wmctrl -i -r "$FWID" -b add,fullscreen
```
