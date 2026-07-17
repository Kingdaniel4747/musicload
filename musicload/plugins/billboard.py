"""Billboard.com plugin for fetching chart songs."""

import logging
import re
from datetime import datetime

import billboard

from musicload.plugins.base import PluginConfig, PluginError, Song

logger = logging.getLogger(__name__)


class BillboardPlugin:
    """Plugin for fetching songs from Billboard charts.

    Supports current charts, historical charts (via date), and year-end charts.
    Converts Billboard chart entries to Song objects for YouTube Music search.

    Chart Names:
        Common charts include 'hot-100', 'pop-songs', 'alternative-songs',
        'r-and-b-songs', etc. See https://www.billboard.com/charts/

    Date Format:
        Historical charts use YYYY-MM-DD format (e.g., '2020-01-15')

    Year-End Charts:
        Use year parameter (e.g., year: 2023) for annual recaps
    """

    @property
    def name(self) -> str:
        return "billboard"

    @property
    def config_schema(self) -> dict:
        return {
            "required": ["chart_name"],
            "optional": {
                "date": None,
                "year": None,
                "limit": None,
                "timeout": 25,
                "max_retries": 5,
            },
        }

    def validate_config(self, config: dict) -> None:
        """Validate configuration."""
        # Required: chart_name
        if "chart_name" not in config:
            raise ValueError("Missing required field: chart_name")

        chart_name = config["chart_name"]
        if not isinstance(chart_name, str):
            raise ValueError("Field 'chart_name' must be a string")

        chart_name = chart_name.strip()
        if not chart_name:
            raise ValueError("Field 'chart_name' cannot be empty")

        # Validate chart_name format (lowercase alphanumeric with hyphens)
        if not re.match(r"^[a-z0-9-]+$", chart_name):
            raise ValueError(
                f"Invalid chart_name: {chart_name}. "
                "Must contain only lowercase letters, numbers, and hyphens "
                "(e.g., 'hot-100', 'pop-songs')"
            )

        # Get current date for validation
        now = datetime.now()
        current_year = now.year

        # Optional: date
        date_provided = "date" in config and config["date"] is not None
        year_provided = "year" in config and config["year"] is not None

        # Check mutual exclusivity
        if date_provided and year_provided:
            raise ValueError("Cannot specify both 'date' and 'year' parameters")

        if date_provided:
            date = config["date"]
            if not isinstance(date, str):
                raise ValueError("Field 'date' must be a string")

            # Validate YYYY-MM-DD format
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                raise ValueError(
                    f"Invalid date format: {date}. Must be YYYY-MM-DD "
                    "(e.g., '2020-01-15')"
                )

            # Validate it's a real date
            try:
                parsed_date = datetime.strptime(date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid date: {date}. {e}")

            # Prevent future dates
            if parsed_date > now:
                raise ValueError(
                    f"Date {date} is in the future. Must be today or earlier"
                )

        # Optional: year
        if year_provided:
            year = config["year"]

            # Accept int or string
            if isinstance(year, str):
                if not year.isdigit():
                    raise ValueError(f"Invalid year: {year}. Must be a number")
                year = int(year)
            elif not isinstance(year, int):
                raise ValueError("Field 'year' must be an integer or numeric string")

            # Validate year range
            if year < 1900 or year > current_year:
                raise ValueError(
                    f"Invalid year: {year}. Must be between 1900 and {current_year}"
                )

        # Optional: limit
        if "limit" in config and config["limit"] is not None:
            limit = config["limit"]
            if not isinstance(limit, int):
                raise ValueError("Field 'limit' must be an integer")
            if limit < 1:
                raise ValueError("Field 'limit' must be positive (>= 1)")
            if limit > 200:
                raise ValueError(
                    "Field 'limit' must be <= 200 (Billboard charts have max 100-200 entries)"
                )

        # Optional: timeout
        if "timeout" in config:
            timeout = config["timeout"]
            if not isinstance(timeout, (int, float)):
                raise ValueError("Field 'timeout' must be a number")
            if timeout <= 0:
                raise ValueError("Field 'timeout' must be positive")
            if timeout > 300:
                raise ValueError("Field 'timeout' must be <= 300 seconds")

        # Optional: max_retries
        if "max_retries" in config:
            max_retries = config["max_retries"]
            if not isinstance(max_retries, int):
                raise ValueError("Field 'max_retries' must be an integer")
            if max_retries < 0:
                raise ValueError("Field 'max_retries' must be non-negative")
            if max_retries > 10:
                raise ValueError("Field 'max_retries' must be <= 10")

    def fetch_songs(self, config: PluginConfig) -> list[Song]:
        """Fetch songs from Billboard chart."""
        # Extract configuration
        chart_name = config.config["chart_name"]
        date = config.config.get("date")
        year = config.config.get("year")
        limit = config.config.get("limit")
        timeout = config.config.get("timeout", 25)
        max_retries = config.config.get("max_retries", 5)

        # Convert year to string if provided as int
        if year is not None and isinstance(year, int):
            year = str(year)

        # Build descriptive log message
        params = []
        if date:
            params.append(f"date={date}")
        if year:
            params.append(f"year={year}")
        if limit:
            params.append(f"limit={limit}")

        logger.info(
            "Fetching Billboard chart: %s%s",
            chart_name,
            f" ({', '.join(params)})" if params else "",
        )

        try:
            # Fetch chart data from Billboard
            chart = billboard.ChartData(
                chart_name,
                date=date,
                year=year,
                timeout=timeout,
                max_retries=max_retries,
            )

            # Check if chart has entries
            if not chart.entries:
                logger.warning(
                    "No entries found for chart %s (date=%s, year=%s)",
                    chart_name,
                    date,
                    year,
                )
                return []

            # Apply limit if specified
            entries = chart.entries if limit is None else chart.entries[:limit]

            # Convert ChartEntry objects to Song objects
            songs = []
            skipped = 0

            for entry in entries:
                # Billboard entries should have artist and title
                # Skip any malformed entries
                if not entry.artist or not entry.title:
                    logger.warning(
                        "Skipping entry with missing data: artist=%s, title=%s",
                        entry.artist,
                        entry.title,
                    )
                    skipped += 1
                    continue

                songs.append(
                    Song(
                        artist=entry.artist,
                        title=entry.title,
                        album=None,  # Not available in Billboard data
                        duration_seconds=None,  # Not available
                    )
                )

            logger.info(
                "Parsed %d songs from Billboard %s chart%s",
                len(songs),
                chart_name,
                f" (skipped {skipped} malformed entries)" if skipped > 0 else "",
            )

            return songs

        except Exception as e:
            error_msg = str(e).lower()

            # Provide specific error messages for common cases
            if "404" in error_msg or "not found" in error_msg:
                raise PluginError(
                    f"Chart not found: {chart_name}. "
                    "Check valid chart names at https://www.billboard.com/charts/"
                )
            elif "timeout" in error_msg:
                raise PluginError(
                    f"Request timed out after {timeout}s. "
                    "Try increasing timeout or check network connection."
                )
            elif "connection" in error_msg or "network" in error_msg:
                raise PluginError(f"Network error while fetching chart: {e}")
            elif "date" in error_msg or "year" in error_msg:
                raise PluginError(
                    f"Invalid date or year for chart {chart_name}: {e}. "
                    "Chart may not have data for the specified date/year."
                )
            else:
                raise PluginError(f"Error fetching Billboard chart {chart_name}: {e}")
