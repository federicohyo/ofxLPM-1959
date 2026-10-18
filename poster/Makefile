1959_poster_A3.pdf: poster.tex fig/die.jpg
	xelatex -interaction=nonstopmode poster.tex >/dev/null
	@grep -c "Warning" poster.log || true
	mv -f poster.pdf 1959_poster_A3.pdf

clean:
	rm -f poster.aux poster.log poster.out poster.pdf 1959_poster_A3.pdf

.PHONY: clean
