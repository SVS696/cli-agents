#!/bin/bash
# Server control script

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PIDFILE="$DIR/server.pid"
LOGFILE="$DIR/logs/server.log"
PORT=11435

start() {
    if is_running; then
        echo "Server already running (PID: $(cat $PIDFILE 2>/dev/null || echo 'unknown'))"
        return
    fi

    mkdir -p "$DIR/logs"
    nohup python3 "$DIR/ollama_compat_server.py" --port $PORT >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1

    if is_running; then
        echo "Server started (PID: $!)"
    else
        echo "Failed to start server. Check logs: $LOGFILE"
        rm -f "$PIDFILE"
    fi
}

stop() {
    if [ -f "$PIDFILE" ]; then
        kill $(cat "$PIDFILE") 2>/dev/null
        rm -f "$PIDFILE"
    fi

    # Also kill by port if still running
    lsof -ti :$PORT | xargs kill 2>/dev/null
    echo "Server stopped"
}

is_running() {
    # Check by port - most reliable
    lsof -ti :$PORT > /dev/null 2>&1
}

status() {
    if is_running; then
        PID=$(lsof -ti :$PORT | head -1)
        echo "Running (PID: $PID, port: $PORT)"
        curl -s http://localhost:$PORT/health 2>/dev/null | python3 -m json.tool 2>/dev/null || true
    else
        echo "Not running"
    fi
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    logs)    tail -f "$LOGFILE" ;;
    *)       echo "Usage: $0 {start|stop|restart|status|logs}" ;;
esac
