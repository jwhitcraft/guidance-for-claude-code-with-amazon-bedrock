# ABOUTME: Lambda function to display token usage by model over time as a stacked area chart
# ABOUTME: Reads pre-resolved MODEL_RATE data from DynamoDB (model names resolved by metrics_aggregator)

import json
import boto3
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key
sys.path.append('/opt')
from widget_utils import parse_widget_context, get_time_range_iso, get_time_range_with_dt, calculate_time_bucket_size, check_describe_mode
from html_utils import generate_error_html
from format_utils import format_number


MODEL_COLORS = {
    "Opus 4.7": "#0d9488",
    "Opus 4.6": "#14b8a6",
    "Opus 4.5": "#2dd4bf",
    "Opus 4.1": "#3b82f6",
    "Opus 4": "#f97316",
    "Opus": "#8b5cf6",
    "Sonnet 4.6": "#7c3aed",
    "Sonnet 4.5": "#a855f7",
    "Sonnet 4": "#10b981",
    "Sonnet 3.7": "#ef4444",
    "Sonnet 3.5": "#ec4899",
    "Sonnet": "#06b6d4",
    "Haiku 4.5": "#f59e0b",
    "Haiku 4": "#d97706",
    "Haiku 3.5": "#8b5cf6",
    "Haiku 3.0": "#6366f1",
    "Haiku": "#84cc16",
}

FALLBACK_COLORS = [
    "#6366f1", "#8b5cf6", "#a78bfa", "#c084fc",
    "#e879f9", "#f472b6", "#fb7185", "#f87171",
]


def get_color(model_name, idx):
    return MODEL_COLORS.get(model_name, FALLBACK_COLORS[idx % len(FALLBACK_COLORS)])


def lambda_handler(event, context):
    if check_describe_mode(event):
        return {"markdown": "# Token Usage by Model Over Time\nStacked area chart of token consumption by model"}

    region = os.environ["METRICS_REGION"]
    metrics_table_name = os.environ.get("METRICS_TABLE", "ClaudeCodeMetrics")

    widget_ctx = parse_widget_context(event)
    width = widget_ctx['width']
    height = widget_ctx['height']
    time_range = widget_ctx['time_range']

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(metrics_table_name)

    try:
        start_iso, end_iso = get_time_range_iso(time_range, default_hours=7*24)
        start_ms, end_ms, start_dt, end_dt = get_time_range_with_dt(time_range, default_hours=7*24)
        bucket_minutes = calculate_time_bucket_size(start_dt, end_dt)

        # Query all MODEL_RATE items in time range
        all_items = []
        response = table.query(
            KeyConditionExpression=Key('pk').eq('METRICS') &
                                 Key('sk').between(f'{start_iso}#MODEL_RATE#',
                                                   f'{end_iso}#MODEL_RATE#~')
        )
        all_items.extend(response.get('Items', []))

        while 'LastEvaluatedKey' in response:
            response = table.query(
                KeyConditionExpression=Key('pk').eq('METRICS') &
                                     Key('sk').between(f'{start_iso}#MODEL_RATE#',
                                                       f'{end_iso}#MODEL_RATE#~'),
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            all_items.extend(response.get('Items', []))

        if not all_items:
            return """
            <div style="
                display: flex; align-items: center; justify-content: center;
                height: 100%; color: #9ca3af; font-size: 14px;
                font-family: 'Amazon Ember', -apple-system, sans-serif;
            ">No model usage data available for this period</div>
            """

        # Bucket items by time interval and model
        # model_buckets: { bucket_dt_str: { model_name: total_tokens } }
        model_buckets = defaultdict(lambda: defaultdict(float))
        all_models = set()

        for item in all_items:
            model_name = item.get('model', 'Unknown')
            tokens = float(item.get('tpm', Decimal(0)))
            ts_str = item.get('timestamp', '')

            if not ts_str or tokens <= 0:
                continue

            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                # Round down to bucket boundary
                total_minutes = dt.hour * 60 + dt.minute
                bucket_minute = (total_minutes // bucket_minutes) * bucket_minutes
                bucket_dt = dt.replace(
                    hour=bucket_minute // 60,
                    minute=bucket_minute % 60,
                    second=0, microsecond=0
                )
                bucket_key = bucket_dt.isoformat()

                model_buckets[bucket_key][model_name] += tokens
                all_models.add(model_name)
            except Exception as e:
                print(f"Error parsing timestamp {ts_str}: {e}")

        if not model_buckets:
            return """
            <div style="
                display: flex; align-items: center; justify-content: center;
                height: 100%; color: #9ca3af; font-size: 14px;
                font-family: 'Amazon Ember', -apple-system, sans-serif;
            ">No model usage data available for this period</div>
            """

        # Sort time buckets and models
        sorted_buckets = sorted(model_buckets.keys())
        sorted_models = sorted(all_models, key=lambda m: sum(
            model_buckets[b].get(m, 0) for b in sorted_buckets
        ), reverse=True)

        # Build stacked data for SVG
        chart_width = max(width - 120, 200)
        chart_height = max(height - 80, 100)
        margin_left = 60
        margin_top = 10
        margin_bottom = 40

        num_buckets = len(sorted_buckets)
        bar_width = max(chart_width / max(num_buckets, 1) - 2, 4)

        # Find max stacked total for Y-axis scaling
        max_total = 0
        for bucket in sorted_buckets:
            total = sum(model_buckets[bucket].get(m, 0) for m in sorted_models)
            max_total = max(max_total, total)

        if max_total == 0:
            max_total = 1

        # Build SVG stacked bars
        bars_svg = ""
        for i, bucket in enumerate(sorted_buckets):
            x = margin_left + i * (chart_width / num_buckets)
            y_offset = 0

            for j, model in enumerate(reversed(sorted_models)):
                tokens = model_buckets[bucket].get(model, 0)
                if tokens <= 0:
                    continue

                bar_h = (tokens / max_total) * chart_height
                y = margin_top + chart_height - y_offset - bar_h
                color = get_color(model, len(sorted_models) - 1 - j)

                bars_svg += (
                    f'<rect x="{x:.1f}" y="{y:.1f}" '
                    f'width="{bar_width:.1f}" height="{bar_h:.1f}" '
                    f'fill="{color}" opacity="0.85">'
                    f'<title>{model}: {format_number(tokens)} tokens</title>'
                    f'</rect>\n'
                )
                y_offset += bar_h

        # Y-axis labels
        y_labels_svg = ""
        num_y_ticks = 4
        for i in range(num_y_ticks + 1):
            val = max_total * i / num_y_ticks
            y = margin_top + chart_height - (i / num_y_ticks) * chart_height
            y_labels_svg += (
                f'<text x="{margin_left - 8}" y="{y + 4}" '
                f'text-anchor="end" font-size="10" fill="#6b7280">'
                f'{format_number(val)}</text>\n'
            )
            y_labels_svg += (
                f'<line x1="{margin_left}" y1="{y}" '
                f'x2="{margin_left + chart_width}" y2="{y}" '
                f'stroke="#e5e7eb" stroke-width="0.5"/>\n'
            )

        # X-axis labels (show a subset to avoid crowding)
        x_labels_svg = ""
        max_x_labels = min(8, num_buckets)
        label_step = max(1, num_buckets // max_x_labels)
        for i in range(0, num_buckets, label_step):
            x = margin_left + i * (chart_width / num_buckets) + bar_width / 2
            y = margin_top + chart_height + 16
            try:
                dt = datetime.fromisoformat(sorted_buckets[i])
                label = dt.strftime("%H:%M")
                # Show date if range spans multiple days
                if (end_dt - start_dt).days >= 1:
                    label = dt.strftime("%m/%d %H:%M")
            except Exception:
                label = sorted_buckets[i][:5]
            x_labels_svg += (
                f'<text x="{x:.1f}" y="{y}" '
                f'text-anchor="middle" font-size="10" fill="#6b7280">'
                f'{label}</text>\n'
            )

        # Legend
        legend_html = ""
        for idx, model in enumerate(sorted_models):
            color = get_color(model, idx)
            total = sum(model_buckets[b].get(model, 0) for b in sorted_buckets)
            legend_html += (
                f'<span style="display:inline-flex; align-items:center; '
                f'margin-right:12px; margin-bottom:4px; font-size:11px; '
                f'color:#374151;">'
                f'<span style="display:inline-block; width:10px; height:10px; '
                f'border-radius:2px; background:{color}; margin-right:4px;"></span>'
                f'{model} ({format_number(total)})'
                f'</span>'
            )

        svg_width = margin_left + chart_width + 20
        svg_height = margin_top + chart_height + margin_bottom

        return f"""
        <div style="
            padding: 8px;
            height: 100%;
            background: white;
            font-family: 'Amazon Ember', -apple-system, sans-serif;
            border-radius: 8px;
            box-sizing: border-box;
            overflow: hidden;
        ">
            <svg width="100%" viewBox="0 0 {svg_width} {svg_height}"
                 preserveAspectRatio="xMidYMid meet"
                 style="display:block;">
                {y_labels_svg}
                {bars_svg}
                {x_labels_svg}
            </svg>
            <div style="
                padding: 4px 8px;
                text-align: center;
                flex-wrap: wrap;
                display: flex;
                justify-content: center;
            ">
                {legend_html}
            </div>
        </div>
        """

    except Exception as e:
        return generate_error_html(str(e))
