# 1959 — the explainer video. Storyboard and plan: PLAN.md
#
#   make assets     fetch the film, copy stills, build contact sheets
#   make narration  generate the voice WAVs (piper, en_US-ryan-high)
#   make cards      render the three 1920x1080 title cards
#   make master     final 16:9 cut   (blocked: needs clip ranges + app capture)
#   make loop       venue loop       (blocked: same)

FF      := ffmpeg -y -v error
YOUTUBE := https://www.youtube.com/watch?v=IOHayh06LJ4
AVDIR   := $(HOME)/tue/NeuromorphicEdgeComputingSystemsLab/slides/animations/aVLSI-Skywater2024
FIGDIR  := ../../paper/RISCV_NeuronArray_2026/figures
PIPER   := voice/.venv/bin/piper
MODEL   := voice/models/en_US-amy-medium.onnx
MODELURL := https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium
HUBEL   := $(HOME)/tue/NeuromorphicEdgeComputingSystemsLab/slides/animations/aVLSI-Skywater2024

.PHONY: assets narration cards master loop all
all: assets narration cards

# ---------- film ----------
assets/HubelWiesel_1959.webm:
	@mkdir -p assets
	yt-dlp -f "bv*+ba/b[height<=1080]" -o "assets/HubelWiesel_1959.%(ext)s" $(YOUTUBE)

# ---------- contact sheets (look, then pick trim ranges) ----------
assets/contact_soma_synapse.png: $(AVDIR)/soma_synapse.mp4
	@mkdir -p assets
	$(FF) -i $< -vf "fps=1/2,scale=300:-2,drawtext=text='%{pts\:hms}':x=4:y=4:fontsize=16:fontcolor=white:box=1:boxcolor=black@0.6,tile=5x6" -frames:v 1 $@

assets/contact_array_loop.png: $(AVDIR)/array_loop160_v0.mp4
	@mkdir -p assets
	$(FF) -i $< -vf "fps=1/4,scale=300:-2,drawtext=text='%{pts\:hms}':x=4:y=4:fontsize=16:fontcolor=white:box=1:boxcolor=black@0.6,tile=5x8" -frames:v 1 $@

# ---------- stills from the paper ----------
assets/%.png assets/%.jpg: $(FIGDIR)/%.png $(FIGDIR)/%.jpg
	@mkdir -p assets
	@cp -f $(FIGDIR)/$*.* assets/ 2>/dev/null || true

assets: assets/HubelWiesel_1959.webm assets/contact_soma_synapse.png \
        assets/contact_array_loop.png \
        assets/silicon_neuron_layout_avlsi2024.png \
        assets/syn_dpi_exc_aVLSI.png assets/netarch.png

# ---------- narration ----------
voice/.venv/bin/piper:
	python3 -m venv voice/.venv
	voice/.venv/bin/pip -q install piper-tts

$(MODEL): voice/.venv/bin/piper
	@mkdir -p voice/models
	curl -sL -o $@ $(MODELURL)/en_US-amy-medium.onnx
	curl -sL -o $@.json $(MODELURL)/en_US-amy-medium.onnx.json

voice/beat%.wav: voice/beats/beat%.txt $(MODEL)
	$(PIPER) -m $(MODEL) -f $@ < $<

narration: $(foreach n,2 3 4 5 6,voice/beat$(n).wav)

# ---------- title cards ----------
cards:
	$(MAKE) -C titles

# ---------- final cut ----------
#
# Eight beat segments, all normalised to 1920x1080/30fps/h264 + aac 48k stereo,
# then concatenated. Durations:
#   b1 8   b2 32   b3 31   b4 15   b5 17   b6 20   b7 8   b8 6   = 137 s
# Narration starts: b2 +1.2, b3 +0.8, b4 +0.5, b5 +0.6, b6 +0.5.
# See make_srt.py for the same table in subtitle form.

FIT := scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p
ENC := -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30 -c:a aac -b:a 192k -ar 48000
TAPE := -f s16le -ar 48000 -ac 2 -i ../bin/data/audio.s16

build:
	mkdir -p build

# b1 -- cold open: red 1959, tape clicks
build/beat1.mp4: titles/card-1.png ../bin/data/audio.s16 Makefile | build
	$(FF) -loop 1 -framerate 30 -i titles/card-1.png \
	  -f s16le -ar 48000 -ac 2 -ss 12 -t 8 -i ../bin/data/audio.s16 \
	  -filter_complex "[0:v]$(FIT)[v];[1:a]volume=3.0,alimiter=limit=0.95[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 8 $@

# b2 -- the 1959 film + narration
build/beat2.mp4: assets/HubelWiesel_1959.webm voice/beat2.wav Makefile | build
	$(FF) -t 32 -i assets/HubelWiesel_1959.webm -i voice/beat2.wav \
	  -filter_complex "[0:v]$(FIT)[v];\
	    [0:a]volume=0.45[bed];\
	    [1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=1200|1200[nar];\
	    [bed][nar]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 32 $@

# b3 -- the chip: die micrograph -> the soma animation (membrane, leak, threshold).
# The neuron-circuit schematic that sat between them is gone; its seconds went
# to the animation the narration was describing all along.
build/beat3.mp4: assets/die.jpg voice/beat3.wav ../bin/data/audio.s16 Makefile | build
	$(FF) -i assets/die.jpg \
	  -ss 0 -t 28 -i $(AVDIR)/soma_synapse.mp4 -i voice/beat3.wav \
	  -f s16le -ar 48000 -ac 2 -t 37 -i ../bin/data/audio.s16 \
	  -filter_complex "\
	    [0:v]scale=2400:1350:force_original_aspect_ratio=decrease,pad=2400:1350:(ow-iw)/2:(oh-ih)/2,zoompan=z='1+0.10*on/270':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=270:s=1920x1080:fps=30,setsar=1,format=yuv420p[v0];\
	    [1:v]$(FIT)[v1];\
	    [v0][v1]concat=n=2:v=1:a=0[v];\
	    [2:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=800|800[nar];\
	    [3:a]volume=1.2[bed];\
	    [bed][nar]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 37 $@

# b4 -- the synapse: the silicon layout held for the whole beat. The two white
# circuit schematics it used to open with are kept at build/beat4_with_schematics.mp4.
build/beat4.mp4: assets/silicon_neuron_layout_avlsi2024.png voice/beat4.wav ../bin/data/audio.s16 Makefile | build
	$(FF) -i assets/silicon_neuron_layout_avlsi2024.png \
	  -i voice/beat4.wav -f s16le -ar 48000 -ac 2 -t 21 -i ../bin/data/audio.s16 \
	  -filter_complex "\
	    [0:v]scale=2400:1350:force_original_aspect_ratio=decrease,pad=2400:1350:(ow-iw)/2:(oh-ih)/2,zoompan=z='1+0.10*on/630':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=630:s=1920x1080:fps=30,setsar=1,format=yuv420p[v];\
	    [1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=500|500[nar];\
	    [2:a]volume=1.2[bed];\
	    [bed][nar]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 21 $@

# b5 -- the array: the 16-neuron array animation, held for the whole beat
# (the layout still that used to open it moved into beat 4)
build/beat5.mp4: voice/beat5.wav ../bin/data/audio.s16 Makefile | build
	$(FF) -ss 29 -t 21 -i $(AVDIR)/array_loop160_v0.mp4 -i voice/beat5.wav \
	  -f s16le -ar 48000 -ac 2 -t 21 -i ../bin/data/audio.s16 \
	  -filter_complex "\
	    [0:v]$(FIT)[v];\
	    [1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=600|600[nar];\
	    [2:a]volume=1.2[bed];\
	    [bed][nar]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 21 $@

# b6 -- the piece: the running installation, binaural (tape left, silicon right)
build/beat6.mp4: assets/app_capture.mp4 voice/beat6.wav Makefile | build
	$(FF) -ss 4 -t 20 -i assets/app_capture.mp4 -i voice/beat6.wav \
	  -filter_complex "[0:v]$(FIT)[v];\
	    [0:a]pan=stereo|c0=0.95*c0|c1=0.40*c1[app];\
	    [1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=500|500[nar];\
	    [app][nar]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 20 $@

# b7 -- coda: the quote, clicks fading
build/beat7.mp4: titles/card-2.png ../bin/data/audio.s16 Makefile | build
	$(FF) -loop 1 -framerate 30 -i titles/card-2.png \
	  -f s16le -ar 48000 -ac 2 -ss 40 -t 8 -i ../bin/data/audio.s16 \
	  -filter_complex "[0:v]$(FIT)[v];\
	    [1:a]volume=2.5,afade=t=out:st=3:d=5[a]" \
	  -map "[v]" -map "[a]" $(ENC) -t 8 $@

# b8 -- credits
build/beat8.mp4: titles/card-3.png Makefile | build
	$(FF) -loop 1 -framerate 30 -i titles/card-3.png \
	  -f lavfi -t 6 -i anullsrc=r=48000:cl=stereo \
	  -filter_complex "[0:v]$(FIT)[v]" \
	  -map "[v]" -map 1:a $(ENC) -t 6 $@

BEATS := $(foreach n,1 2 3 4 5 6 7 8,build/beat$(n).mp4)

build/master_raw.mp4: $(BEATS)
	printf "file %s\n" $(foreach b,$(BEATS),$(CURDIR)/$(b)) > build/concat.txt
	$(FF) -f concat -safe 0 -i build/concat.txt -c:v copy -c:a aac -b:a 192k $@

SUBS = subtitles=1959.srt:fontsdir=../poster/fonts:force_style='FontName=Work Sans,FontSize=17'

# master: subtitles burned in (plays anywhere, no sidecar needed)
1959_master_1080p.mp4: build/master_raw.mp4 1959.srt
	$(FF) -i build/master_raw.mp4 -vf "$(SUBS)" \
	  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30 -c:a copy $@

# loop for the venue: no credits, same burned subtitles
1959_loop.mp4: build/master_raw.mp4 1959.srt
	$(FF) -i build/master_raw.mp4 -t 147 -vf "$(SUBS)" \
	  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30 -c:a copy $@

1959.srt: make_srt.py voice/beats/beat2.txt voice/beats/beat3.txt voice/beats/beat4.txt voice/beats/beat5.txt voice/beats/beat6.txt
	python3 make_srt.py > 1959.srt

master: 1959_master_1080p.mp4 1959.srt
	@echo "master: 1959_master_1080p.mp4 + 1959.srt"

loop: 1959_loop.mp4
