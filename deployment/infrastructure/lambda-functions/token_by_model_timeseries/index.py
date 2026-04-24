# ABOUTME: Lambda function to display token usage by model over time as a stacked area chart
# ABOUTME: Reads MODEL_RATE data from DynamoDB, resolving ARNs via Bedrock ListFoundationModels

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
_display_name_cache = {}

_APPLICATION_PROFILE_ARN_RE = re.compile(
    r"^arn:aws:bedrock:(?P<region>[^:]+):[^:]*:application-inference-profile/[^/]+$"
)

_FOUNDATION_MODEL_ARN_RE = re.compile(
    r"^arn:aws:bedrock:[^:]*:[^:]*:foundation-model/(?P<model>.+)$"
)


def _load_display_name_cache(region):
    """Populate _display_name_cache from Bedrock ListFoundationModels (called once per cold start)."""
    if _display_name_cache:
        return
    try:
        bedrock = boto3.client("bedrock", region_name=region)
        response = bedrock.list_foundation_models(byProvider="Anthropic")
        for model in response.get("modelSummaries", []):
            model_id = model.get("modelId", "")
            model_name = model.get("modelName", "")
            if model_id and model_name:
                display = re.sub(r"(?i)^claude\s+", "", model_name).strip()
                _display_name_cache[model_id] = display
        print(f"Loaded {len(_display_name_cache)} Anthropic model display names from Bedrock")
    except Exception as e:
        print(f"Failed to load foundation model names: {e}")


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


def get_display_name(model_id, region=None):
    """Look up display name from Bedrock's ListFoundationModels cache."""
    if region:
        _load_display_name_cache(region)

    if model_id in _display_name_cache:
        return _display_name_cache[model_id]

    stripped = re.sub(r"^(us|eu|apac)\.", "", model_id)
    if stripped in _display_name_cache:
        return _display_name_cache[stripped]

    if model_id in MODEL_COLORS:
        return model_id

    display = model_id
    for prefix in ["us.anthropic.", "eu.anthropic.", "apac.anthropic.", "anthropic."]:
        display = display.replace(prefix, "")
    return display


def normalize_model_name(raw_name, region=None):
    """Resolve ARN if needed, then map to display name. Handles both old and new data."""
    resolved = resolve_model_id(raw_name)
    return get_display_name(resolved, region=region)


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

        # Bucket items by time interval and model (resolving legacy ARNs)
        model_buckets = defaultdict(lambda: defaultdict(float))
        all_models = set()

        for item in all_items:
            raw_model = item.get('model', 'Unknown')
            model_name = normalize_model_name(raw_model, region=region)
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

        num_buckets = len(sorted_buckets)

        # Find max stacked total for Y-axis scaling
        max_total = 0
        for bucket in sorted_buckets:
            total = sum(model_buckets[bucket].get(m, 0) for m in sorted_models)
            max_total = max(max_total, total)

        if max_total == 0:
            max_total = 1

        def x_for(i):
            return margin_left + (i / max(num_buckets - 1, 1)) * chart_width

        def y_for(val):
            return margin_top + chart_height - (val / max_total) * chart_height

        baseline_y = margin_top + chart_height

        # Pre-compute stacked Y values per bucket
        # Stack order: reversed sorted_models (lowest-usage on bottom)
        stack_order = list(reversed(sorted_models))
        cumulative = [[0.0] * num_buckets for _ in stack_order]
        for layer_idx, model in enumerate(stack_order):
            for bi, bucket in enumerate(sorted_buckets):
                below = cumulative[layer_idx - 1][bi] if layer_idx > 0 else 0.0
                cumulative[layer_idx][bi] = below + model_buckets[bucket].get(model, 0)

        # Build SVG stacked area paths (bottom-up, so earlier layers render behind)
        areas_svg = ""
        for layer_idx, model in enumerate(stack_order):
            color = get_color(model, len(stack_order) - 1 - layer_idx)

            top_points = []
            bottom_points = []
            for bi in range(num_buckets):
                px = x_for(bi)
                top_val = cumulative[layer_idx][bi]
                bot_val = cumulative[layer_idx - 1][bi] if layer_idx > 0 else 0.0
                top_points.append(f"{px:.1f},{y_for(top_val):.1f}")
                bottom_points.append(f"{px:.1f},{y_for(bot_val):.1f}")

            top_path = " L".join(top_points)
            bottom_path = " L".join(reversed(bottom_points))
            areas_svg += (
                f'<path d="M{top_path} L{bottom_path} Z" '
                f'fill="{color}" opacity="0.75"/>\n'
            )
            # Stroke line on top edge for definition
            areas_svg += (
                f'<path d="M{top_path}" '
                f'fill="none" stroke="{color}" stroke-width="1.5" opacity="0.9"/>\n'
            )

        # Y-axis labels and grid lines
        y_labels_svg = ""
        num_y_ticks = 4
        for i in range(num_y_ticks + 1):
            val = max_total * i / num_y_ticks
            y = y_for(val)
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
            x = x_for(i)
            y = baseline_y + 14
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
            if legend_x > margin_left + chart_width - 40:
                legend_x = margin_left
                legend_y += 14

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
                {areas_svg}
                {x_labels_svg}
                {legend_svg}
            </svg>
        </div>
        """

    except Exception as e:
        return generate_error_html(str(e))
