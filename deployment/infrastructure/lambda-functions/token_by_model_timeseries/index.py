# ABOUTME: Lambda function to display token usage by model over time as a stacked bar chart
# ABOUTME: Reads MODEL_RATE data from DynamoDB, resolving any legacy ARNs at read time

import re
import boto3
import os
import sys
from collections import defaultdict
from datetime import datetime
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

# --- Inference profile resolution for legacy DynamoDB items ---

_inference_profile_cache = {}

_APPLICATION_PROFILE_ARN_RE = re.compile(
    r"^arn:aws:bedrock:(?P<region>[^:]+):[^:]*:application-inference-profile/[^/]+$"
)

_FOUNDATION_MODEL_ARN_RE = re.compile(
    r"^arn:aws:bedrock:[^:]*:[^:]*:foundation-model/(?P<model>.+)$"
)


def resolve_model_id(model_id):
    """Resolve an application-inference-profile ARN to its foundation model ID."""
    if not model_id:
        return model_id

    match = _APPLICATION_PROFILE_ARN_RE.match(model_id)
    if not match:
        return model_id

    if model_id in _inference_profile_cache:
        return _inference_profile_cache[model_id]

    region = match.group("region")
    try:
        bedrock = boto3.client("bedrock", region_name=region)
        profile = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
        for model in profile.get("models", []):
            model_arn = model.get("modelArn", "")
            fm_match = _FOUNDATION_MODEL_ARN_RE.match(model_arn)
            if fm_match:
                resolved = fm_match.group("model")
                _inference_profile_cache[model_id] = resolved
                return resolved
    except Exception as e:
        print(f"Failed to resolve inference profile {model_id}: {e}")

    _inference_profile_cache[model_id] = model_id
    return model_id


def get_display_name(model_id):
    """Convert a model ID (or already-resolved display name) to a short display name."""
    display = model_id.replace("us.anthropic.", "").replace("eu.anthropic.", "").replace("apac.anthropic.", "").replace("anthropic.", "")
    lower = display.lower()

    for pattern, name in [
        ("opus-4-7", "Opus 4.7"), ("opus-4.7", "Opus 4.7"),
        ("opus-4-6", "Opus 4.6"), ("opus-4.6", "Opus 4.6"),
        ("opus-4-5", "Opus 4.5"), ("opus-4.5", "Opus 4.5"),
        ("opus-4-1", "Opus 4.1"), ("opus-4.1", "Opus 4.1"),
        ("opus-4", "Opus 4"),
        ("sonnet-4-6", "Sonnet 4.6"), ("sonnet-4.6", "Sonnet 4.6"),
        ("sonnet-4-5", "Sonnet 4.5"), ("sonnet-4.5", "Sonnet 4.5"),
        ("sonnet-4", "Sonnet 4"),
        ("sonnet-3.7", "Sonnet 3.7"), ("sonnet-3-7", "Sonnet 3.7"),
        ("sonnet-3.5", "Sonnet 3.5"), ("sonnet-3-5", "Sonnet 3.5"),
        ("haiku-4-5", "Haiku 4.5"), ("haiku-4.5", "Haiku 4.5"),
        ("haiku-4", "Haiku 4"),
        ("haiku-3.5", "Haiku 3.5"), ("haiku-3-5", "Haiku 3.5"),
        ("haiku-3", "Haiku 3.0"),
    ]:
        if pattern in lower:
            return name

    for family in ["opus", "sonnet", "haiku"]:
        if family in lower:
            return family.capitalize()

    # Already a display name like "Opus 4.5" — return as-is
    if model_id in MODEL_COLORS:
        return model_id

    return display.split("-")[0].capitalize()


def normalize_model_name(raw_name):
    """Resolve ARN if needed, then map to display name. Handles both old and new data."""
    resolved = resolve_model_id(raw_name)
    return get_display_name(resolved)


def get_color(model_name, idx):
    return MODEL_COLORS.get(model_name, FALLBACK_COLORS[idx % len(FALLBACK_COLORS)])


def lambda_handler(event, context):
    if check_describe_mode(event):
        return {"markdown": "# Token Usage by Model Over Time\nStacked bar chart of token consumption by model"}

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

        # Bucket items by time interval and model (resolving legacy ARNs)
        model_buckets = defaultdict(lambda: defaultdict(float))
        all_models = set()

        for item in all_items:
            raw_model = item.get('model', 'Unknown')
            model_name = normalize_model_name(raw_model)
            tokens = float(item.get('tpm', Decimal(0)))
            ts_str = item.get('timestamp', '')

            if not ts_str or tokens <= 0:
                continue

            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
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

        # Sort time buckets and models by total usage descending
        sorted_buckets = sorted(model_buckets.keys())
        sorted_models = sorted(all_models, key=lambda m: sum(
            model_buckets[b].get(m, 0) for b in sorted_buckets
        ), reverse=True)

        # Chart dimensions — use fixed viewBox, let SVG scale
        chart_width = 560
        chart_height = 160
        margin_left = 55
        margin_top = 5
        margin_bottom = 30
        legend_height = 24

        num_buckets = len(sorted_buckets)
        bar_gap = 1
        bar_width = max((chart_width - num_buckets * bar_gap) / max(num_buckets, 1), 3)

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
            x = margin_left + i * (bar_width + bar_gap)
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

        # Y-axis labels and grid lines
        y_labels_svg = ""
        num_y_ticks = 4
        for i in range(num_y_ticks + 1):
            val = max_total * i / num_y_ticks
            y = margin_top + chart_height - (i / num_y_ticks) * chart_height
            y_labels_svg += (
                f'<text x="{margin_left - 6}" y="{y + 3}" '
                f'text-anchor="end" font-size="9" fill="#9ca3af">'
                f'{format_number(val)}</text>\n'
            )
            if i > 0:
                y_labels_svg += (
                    f'<line x1="{margin_left}" y1="{y}" '
                    f'x2="{margin_left + chart_width}" y2="{y}" '
                    f'stroke="#374151" stroke-opacity="0.15" stroke-width="0.5"/>\n'
                )

        # Baseline
        baseline_y = margin_top + chart_height
        y_labels_svg += (
            f'<line x1="{margin_left}" y1="{baseline_y}" '
            f'x2="{margin_left + chart_width}" y2="{baseline_y}" '
            f'stroke="#6b7280" stroke-width="0.5"/>\n'
        )

        # X-axis labels
        x_labels_svg = ""
        max_x_labels = min(6, num_buckets)
        label_step = max(1, num_buckets // max_x_labels)
        for i in range(0, num_buckets, label_step):
            x = margin_left + i * (bar_width + bar_gap) + bar_width / 2
            y = margin_top + chart_height + 14
            try:
                dt = datetime.fromisoformat(sorted_buckets[i])
                if (end_dt - start_dt).days >= 1:
                    label = dt.strftime("%m/%d %H:%M")
                else:
                    label = dt.strftime("%H:%M")
            except Exception:
                label = sorted_buckets[i][:5]
            x_labels_svg += (
                f'<text x="{x:.1f}" y="{y}" '
                f'text-anchor="middle" font-size="9" fill="#9ca3af">'
                f'{label}</text>\n'
            )

        # Legend as SVG text (inside the same SVG to avoid layout overflow)
        legend_svg = ""
        legend_x = margin_left
        legend_y = margin_top + chart_height + margin_bottom + 2
        for idx, model in enumerate(sorted_models):
            color = get_color(model, idx)
            total = sum(model_buckets[b].get(model, 0) for b in sorted_buckets)
            label = f"{model} ({format_number(total)})"
            legend_svg += (
                f'<rect x="{legend_x}" y="{legend_y - 7}" '
                f'width="8" height="8" rx="1" fill="{color}"/>\n'
                f'<text x="{legend_x + 11}" y="{legend_y}" '
                f'font-size="9" fill="#9ca3af">{label}</text>\n'
            )
            legend_x += len(label) * 5.5 + 22
            # Wrap to next line if we'd overflow
            if legend_x > margin_left + chart_width - 40:
                legend_x = margin_left
                legend_y += 14
                legend_height_extra = 14

        svg_width = margin_left + chart_width + 10
        svg_height = legend_y + 10

        return f"""
        <div style="
            padding: 4px;
            height: 100%;
            background: transparent;
            font-family: 'Amazon Ember', -apple-system, sans-serif;
            border-radius: 8px;
            box-sizing: border-box;
        ">
            <svg width="100%" viewBox="0 0 {svg_width} {svg_height}"
                 preserveAspectRatio="xMinYMin meet"
                 style="display:block;">
                {y_labels_svg}
                {bars_svg}
                {x_labels_svg}
                {legend_svg}
            </svg>
        </div>
        """

    except Exception as e:
        return generate_error_html(str(e))
