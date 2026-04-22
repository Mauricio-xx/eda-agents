# Build the tutorial PDF.
# Requirements: texlive (beamer, tikz, listings, hyperref, xcolor,
#               metropolis theme).

MAIN       := main
NOTES_MAIN := main-with-notes
LATEXMK    ?= latexmk

.PHONY: all pdf notes clean distclean watch docker docker-notes

all: pdf

# Audience PDF: slides only, notes hidden.
pdf:
	$(LATEXMK) -pdf -halt-on-error -interaction=nonstopmode $(MAIN).tex

# Speaker PDF: slide + notes on the right-hand page.
notes:
	$(LATEXMK) -pdf -halt-on-error -interaction=nonstopmode $(NOTES_MAIN).tex

# Continuous rebuild on save (needs fswatch / inotify via latexmk -pvc).
watch:
	$(LATEXMK) -pdf -pvc -halt-on-error -interaction=nonstopmode $(MAIN).tex

clean:
	$(LATEXMK) -c
	-$(LATEXMK) -c $(NOTES_MAIN).tex
	rm -f *.nav *.snm *.vrb *.bbl *.run.xml
	rm -f sections/*.aux

distclean: clean
	$(LATEXMK) -C
	-$(LATEXMK) -C $(NOTES_MAIN).tex
	rm -f $(MAIN).pdf $(NOTES_MAIN).pdf

# Fallback: build in a disposable texlive container.
docker:
	docker run --rm -v $$PWD:/work -w /work texlive/texlive:latest \
	    latexmk -pdf -halt-on-error -interaction=nonstopmode $(MAIN).tex

docker-notes:
	docker run --rm -v $$PWD:/work -w /work texlive/texlive:latest \
	    latexmk -pdf -halt-on-error -interaction=nonstopmode $(NOTES_MAIN).tex
