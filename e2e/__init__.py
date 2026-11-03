"""Headless-browser checks against a running server.

They are skipped unless CINEMA_E2E=1. Set CINEMA_BASE (default http://127.0.0.1:<this app's port>),
CINEMA_EDGE (the msedge.exe path) and, for the watch-page checks, CINEMA_WATCH (a path to a watch page
whose episode is downloaded, e.g. /tv/watch/mmov/513091?ep=3). Every non-GET request is blocked, so no
favourite, history entry or download is ever touched; only the browser started here is killed.

    set CINEMA_E2E=1
    python -m unittest discover -s e2e -t . -v
"""
