# 1959 — title cards: titles.tex -> card-{1,2,3}.png (1920x1080)
all: card-1.png card-2.png card-3.png

card-%.png: titles.pdf
	pdftoppm -png -r 192 titles.pdf card

titles.pdf: titles.tex ../../poster/fonts/WorkSans-Regular.ttf
	xelatex -interaction=nonstopmode -halt-on-error titles.tex >/dev/null

clean:
	rm -f titles.pdf titles.aux titles.log card-*.png
