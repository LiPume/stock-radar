"""Single-instance Shanghai close scan and two bounded stale-data retry slots."""
from apscheduler.schedulers.background import BackgroundScheduler

from scripts.market_probe import SHANGHAI


def make_scheduler(service):
    scheduler = BackgroundScheduler(timezone=SHANGHAI)
    for trigger, hour, minute in (("scheduled", 15, 45), ("retry_1", 16, 15), ("retry_2", 16, 45)):
        scheduler.add_job(service.scheduled_tick, "cron", args=[trigger], id=trigger,
                          day_of_week="mon-fri", hour=hour, minute=minute,
                          coalesce=True, max_instances=1, misfire_grace_time=900)
    return scheduler
