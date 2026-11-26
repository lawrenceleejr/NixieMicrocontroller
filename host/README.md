# nixiefeed

Host software for the NixieCounter firmware: fetches or models a number and
sends it to the Arduino that drives a frequency-counter Nixie display. See the
repository's top-level README and `docs/` for the full story.

    pip install -e .
    nixiefeed list
    nixiefeed run us_births_today            # terminal preview
    nixiefeed run us_births_today --port /dev/ttyUSB0
