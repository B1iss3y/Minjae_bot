"""투표 시간대 생성과 서버 시간 설정 검증 도우미."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def parse_config_time(value: str) -> tuple[int, int, int]:
    """HH:MM을 (시, 분, 날짜 이동량)으로 변환한다.

    Discord 표시에서는 ``24:00``을 그대로 쓸 수 있지만 실제 datetime은
    다음 날 00:00이어야 한다.
    """

    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("시간은 HH:MM 형식이어야 합니다.") from exc

    if not (0 <= hour <= 24 and 0 <= minute <= 59):
        raise ValueError("시간 범위가 올바르지 않습니다.")
    if hour == 24:
        if minute != 0:
            raise ValueError("24시는 24:00만 사용할 수 있습니다.")
        return 0, 0, 1
    return hour, minute, 0


def build_vote_slots(
    now: datetime,
    day_time: str,
    night_time: str,
    *,
    days: int = 7,
) -> list[dict[str, str]]:
    """다음 ``days``일의 표시 이름과 실제 KST 일시를 함께 생성한다."""

    day_hour, day_minute, day_offset = parse_config_time(day_time)
    night_hour, night_minute, night_offset = parse_config_time(night_time)
    local_now = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
    slots: list[dict[str, str]] = []

    for offset in range(1, days + 1):
        display_date = local_now.date() + timedelta(days=offset)
        weekday = WEEKDAYS[display_date.weekday()]
        for period, configured, hour, minute, date_offset in (
            ("낮", day_time, day_hour, day_minute, day_offset),
            ("밤", night_time, night_hour, night_minute, night_offset),
        ):
            actual_date = display_date + timedelta(days=date_offset)
            actual = datetime.combine(actual_date, time(hour, minute), tzinfo=KST)
            slots.append(
                {
                    "label": f"{display_date:%m/%d}({weekday}) {period} {configured}",
                    "datetime": actual.isoformat(),
                }
            )

    return slots


def slot_datetime(slots: list[dict[str, str]], label: str) -> str:
    """표시 이름에 해당하는 실제 ISO 일시를 반환한다."""

    for slot in slots:
        if slot.get("label") == label:
            return slot["datetime"]
    raise ValueError(f"알 수 없는 투표 시간대입니다: {label}")
