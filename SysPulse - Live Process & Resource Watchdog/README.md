# SysPulse — Live Process & Resource Watchdog

Live terminal dashboard (psutil + rich) that flags processes sustaining high CPU/RAM
and logs each spike episode to SQLite.

## Run
    pip install -r requirements.txt
    python syspulse.py                         # defaults: CPU>80%, MEM>50%, 3 ticks
    python syspulse.py --cpu 60 --ticks 5 --interval 0.5
    python syspulse.py --normalize             # CPU as share of all cores (max 100%)
    python syspulse.py --history 20            # query logged spikes, then exit

Run with admin/root to see all connections and inspect system processes.

## Query the log directly
    sqlite3 syspulse.db "SELECT name, COUNT(*) c, MAX(value) FROM spikes GROUP BY name ORDER BY c DESC;"
