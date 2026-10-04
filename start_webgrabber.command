#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
export PYTHONUTF8=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

show_error() {
  print -u2 -- "$1"
  if [[ -t 0 ]]; then
    read -r "?Tryck Enter för att stänga..."
  fi
  exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
  show_error "Python 3 saknas. Installera Python 3 från python.org och försök igen."
fi

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  show_error "Python saknar Tk-stöd. Installera Python 3 med Tk från python.org. Inget har ändrats i projektmappen."
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Skapar virtuell Python-miljö..."
  python3 -m venv "$VENV_DIR" || show_error "Kunde inte skapa .venv."
elif ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
  show_error ".venv finns men saknar fungerande Tk-stöd. Säkerhets skull togs den inte bort; kontrollera Python-installationen och .venv manuellt."
fi

if ! "$PYTHON_BIN" -c 'import bs4, requests, reportlab' >/dev/null 2>&1; then
  echo "Installerar WebGrabbers Python-beroenden..."
  "$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt" || show_error "Beroenden kunde inte installeras. Kontrollera nätverket och försök igen."
fi

echo "Startar WebGrabber..."
set +e
"$PYTHON_BIN" "$SCRIPT_DIR/main.py"
exit_code=$?
set -e

if (( exit_code != 0 )); then
  show_error "WebGrabber avslutades med felkod $exit_code."
fi

exit 0
