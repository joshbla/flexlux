"""Daily schedule math: how far into "night" the clock is, and what the
warmth and brightness sliders should read at that point.

Four times of day split the day into phases: wind-down (day ramping to
night), night (held), wake-up (night ramping back to day), and day. Times are
minutes after local midnight.
"""

from dataclasses import dataclass

MINUTES_PER_DAY = 24 * 60

DAY = "day"
WIND_DOWN = "wind_down"
NIGHT = "night"
WAKE_UP = "wake_up"


@dataclass(frozen=True)
class DailySchedule:
    wind_down_start: int
    night_start: int
    wake_start: int
    day_start: int

    def _offsets(self):
        """Each boundary's distance after wind-down starts, in minutes."""
        origin = self.wind_down_start
        return tuple((t - origin) % MINUTES_PER_DAY
                     for t in (self.night_start, self.wake_start, self.day_start))

    def is_valid(self):
        """True when the four times run in order around the clock.

        Wind-down and wake-up may each take zero minutes, but night and day
        must both exist: wake-up has to start after night is reached, and day
        has to start after wake-up starts.
        """
        night, wake, day = self._offsets()
        return night <= wake and wake <= day and day > 0

    def phase_and_progress(self, minute):
        """The phase at a minute of the day, and how far into night it is.

        Progress is 0.0 during the day and 1.0 at night, rising linearly
        through wind-down and falling linearly through wake-up.
        """
        night, wake, day = self._offsets()
        t = (minute - self.wind_down_start) % MINUTES_PER_DAY
        if t < night:
            return WIND_DOWN, t / night
        if t < wake:
            return NIGHT, 1.0
        if t < day:
            return WAKE_UP, 1.0 - (t - wake) / (day - wake)
        return DAY, 0.0


def scheduled_warmth(progress, night_level):
    """Warmth slider level (0-100, unrounded) at a night progress."""
    return night_level * progress


def scheduled_brightness(progress, day_value, night_value):
    """Brightness slider value (0-200, unrounded) at a night progress.

    Moves from the monitor's daytime value toward the night value. A monitor
    already set darker than the night value in the day is left alone, so the
    schedule only ever dims.
    """
    if day_value <= night_value:
        return float(day_value)
    return day_value + (night_value - day_value) * progress


def step_toward(current, target, max_step):
    """Move current toward target by at most max_step."""
    if abs(target - current) <= max_step:
        return target
    return current + max_step if target > current else current - max_step
