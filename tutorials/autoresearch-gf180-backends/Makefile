# Build the autoresearch-gf180-backends tutorial PDF.
# Sibling of tutorials/agents-analog-digital/. Uses pdflatex directly
# (latexmk optional) so the build works on minimal TeX Live installs.

MAIN       := main
NOTES_MAIN := main-with-notes
CHIP_MAIN  := main-chipathon
PDFLATEX   ?= pdflatex
PDFTOPPM   ?= pdftoppm
PDF_FLAGS  := -halt-on-error -interaction=nonstopmode

.PHONY: all pdf notes chipathon clean distclean

all: pdf

pdf:
	$(PDFLATEX) $(PDF_FLAGS) $(MAIN).tex
	$(PDFLATEX) $(PDF_FLAGS) $(MAIN).tex

notes:
	$(PDFLATEX) $(PDF_FLAGS) $(NOTES_MAIN).tex
	$(PDFLATEX) $(PDF_FLAGS) $(NOTES_MAIN).tex

chipathon:
	$(PDFLATEX) $(PDF_FLAGS) $(CHIP_MAIN).tex
	$(PDFLATEX) $(PDF_FLAGS) $(CHIP_MAIN).tex
	$(PDFTOPPM) -png -r 300 $(CHIP_MAIN).pdf chipathon-slide

clean:
	rm -f *.aux *.log *.nav *.snm *.vrb *.toc *.out *.bbl *.run.xml
	rm -f sections/*.aux

distclean: clean
	rm -f $(MAIN).pdf $(NOTES_MAIN).pdf $(CHIP_MAIN).pdf
	rm -f chipathon-slide-*.png
