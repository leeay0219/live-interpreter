#!/bin/bash
# Live caption server. Usage: ./start.sh [--event <id>] [extra server.py args]
# The event (events/<id>.toml) needs its vocabularies once: .venv/bin/python setup_event.py <id>
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  tools/bootstrap.sh || exit
fi
exec .venv/bin/python server.py --auto-lang "$@"
