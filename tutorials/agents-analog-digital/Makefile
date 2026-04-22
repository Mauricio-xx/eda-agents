# Build the agents-analog-digital tutorial PDF.
# Mirror of tutorials/rtl2gds-gf180-docker/Makefile.

MAIN       := main
NOTES_MAIN := main-with-notes
LATEXMK    ?= latexmk

.PHONY: all pdf notes clean distclean watch docker docker-notes

all: pdf

pdf:
	$(LATEXMK) -pdf -halt-on-error -interaction=nonstopmode $(MAIN).tex

notes:
	$(LATEXMK) -pdf -halt-on-error -interaction=nonstopmode $(NOTES_MAIN).tex

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

docker:
	docker run --rm -v $$PWD:/work -w /work texlive/texlive:latest \
	    latexmk -pdf -halt-on-error -interaction=nonstopmode $(MAIN).tex

docker-notes:
	docker run --rm -v $$PWD:/work -w /work texlive/texlive:latest \
	    latexmk -pdf -halt-on-error -interaction=nonstopmode $(NOTES_MAIN).tex
