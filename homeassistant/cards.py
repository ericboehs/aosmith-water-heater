"""Lovelace cards for the local CTA-2045 water heater.

Kept apart from the patching script so the card definitions stay readable and
can be regenerated without touching the websocket plumbing.
"""

# Shared colour ramp, matching the palette already used by the battery cards.
HOT_WATER_COLOR = """
{% set p = states('sensor.water_heater_hot_water') | float(0) %}
{% if p >= 80 %}#39ff14
{% elif p >= 60 %}#a6ff4d
{% elif p >= 40 %}#ffe600
{% elif p >= 20 %}#ff9900
{% else %}#ff531a{% endif %}
"""

GLANCE = {
    "type": "grid",
    "columns": 2,
    "square": False,
    "grid_options": {"columns": 12, "rows": "auto"},
    "cards": [
        {
            "type": "custom:mushroom-template-card",
            "entity": "sensor.water_heater_hot_water",
            "icon": "mdi:water-thermometer",
            "primary": "{{ states('sensor.water_heater_hot_water') | float(0) | round(0) }}% hot water",
            # Deficit is what the tank says it still needs; capacity moves with
            # the operating mode, so show both rather than a bare percentage.
            "secondary": (
                "{{ states('sensor.water_heater_energy_deficit') | int(0) }} Wh short"
                " of {{ states('sensor.water_heater_storage_capacity') | int(0) }} Wh"
            ),
            "icon_color": HOT_WATER_COLOR,
            "badge_icon": "{% if is_state('binary_sensor.water_heater_heating','on') %}mdi:fire{% endif %}",
            "badge_color": "#ff9900",
            "layout": "horizontal",
            "fill_container": True,
            "multiline_secondary": False,
            "tap_action": {"action": "more-info"},
        },
        {
            "type": "custom:mushroom-template-card",
            "entity": "sensor.water_heater_operational_state",
            "icon": "mdi:heat-wave",
            "primary": "{{ states('sensor.water_heater_operational_state') }}",
            # The measured clamp reading, not the tank's nameplate figure.
            "secondary": (
                "{{ states('sensor.vue_garage_water_heater_power') | float(0) | round(0) }} W"
                " · {{ relative_time(states.sensor.water_heater_operational_state.last_changed) }} ago"
            ),
            "icon_color": (
                "{% if is_state('binary_sensor.water_heater_sgd_error','on') %}#c90076"
                "{% elif is_state('binary_sensor.water_heater_heating','on') %}#ff9900"
                "{% else %}#444{% endif %}"
            ),
            "layout": "horizontal",
            "fill_container": True,
            "multiline_secondary": False,
            "tap_action": {"action": "more-info"},
        },
    ],
}

CONTROL = {
    "type": "entities",
    "title": "\U0001f6c1 Water Heater",
    "show_header_toggle": False,
    "state_color": True,
    "grid_options": {"columns": 12, "rows": "auto"},
    "entities": [
        {"type": "section", "label": "\U0001f50c Local Control (RS-485)"},
        {
            "entity": "select.water_heater_demand_response",
            "name": "Demand Response",
            "icon": "mdi:transmission-tower",
        },
        {
            "type": "custom:template-entity-row",
            "entity": "sensor.water_heater_operational_state",
            "name": "State",
            "icon": "mdi:state-machine",
            "state": "{{ states('sensor.water_heater_operational_state') }}",
            "secondary": (
                "{{ relative_time(states.sensor.water_heater_operational_state.last_changed) }} ago"
            ),
        },
        {
            "type": "custom:template-entity-row",
            "entity": "sensor.water_heater_hot_water",
            "name": "Hot Water",
            "state": "{{ states('sensor.water_heater_hot_water') }} %",
            "secondary": (
                "{{ states('sensor.water_heater_energy_deficit') | int(0) }}"
                " / {{ states('sensor.water_heater_storage_capacity') | int(0) }} Wh deficit"
            ),
            "card_mod": {
                "style": ":host {\n  --card-mod-icon-color: "
                + " ".join(HOT_WATER_COLOR.split())
                + ";\n}\n"
            },
        },
        {
            "type": "custom:template-entity-row",
            "entity": "sensor.vue_garage_water_heater_power",
            "name": "Power",
            "icon": "mdi:flash",
            "state": "{{ states('sensor.vue_garage_water_heater_power') | float(0) | round(0) }} W",
            # The tank only ever reports a nameplate constant (4500 with the
            # element, 356 on the compressor), so the clamp is the real number.
            "secondary": (
                "tank reports {{ states('sensor.water_heater_nominal_power_draw') | int(0) }} W nameplate"
            ),
        },
        {
            "entity": "sensor.water_heater_setpoint",
            "name": "Setpoint",
            "icon": "mdi:thermometer",
            "secondary_info": "none",
        },
        {
            "type": "conditional",
            "conditions": [
                {"entity": "binary_sensor.water_heater_sgd_error", "state": "on"}
            ],
            "row": {
                "entity": "binary_sensor.water_heater_sgd_error",
                "name": "SGD Error — tank lost the RS-485 link",
                "icon": "mdi:lan-disconnect",
            },
        },
        {"type": "section", "label": "\U0001f916 Automation"},
        {
            "entity": "input_select.water_heater_mode",
            "name": "Mode (panel only)",
            "icon": "mdi:water-thermometer",
        },
        {
            "entity": "input_boolean.water_heater_auto_pause",
            "name": "Pause Auto-Control (2 hrs)",
            "icon": "mdi:pause-circle-outline",
        },
        {
            "type": "conditional",
            "conditions": [
                {"entity": "timer.water_heater_auto_pause_duration", "state": "active"}
            ],
            "row": {
                "type": "custom:timer-bar-card",
                "entity": "timer.water_heater_auto_pause_duration",
                "name": "Pause Timer",
            },
        },
        {
            "entity": "input_boolean.water_heater_auto_control",
            "name": "Auto Control Active",
            "icon": "mdi:lock-open-variant",
        },
        {
            "type": "custom:template-entity-row",
            "entity": "sensor.expected_water_heater_mode",
            "name": "Expected Mode",
            "icon": "mdi:lightbulb-on-outline",
            "state": "{{ states('sensor.expected_water_heater_mode') }}",
            "secondary": "{{ state_attr('sensor.expected_water_heater_mode', 'explanation') }}",
        },
        {"type": "section", "label": "☁️ Cloud (iCOMM)"},
        {"entity": "water_heater.garage", "secondary_info": "last-changed"},
        {
            "type": "custom:template-entity-row",
            "entity": "sensor.garage_hot_water_availability",
            "name": "Hot Water (cloud)",
            "state": "{{ states('sensor.garage_hot_water_availability') }} %",
            "secondary": (
                "{{ relative_time(states.sensor.garage_hot_water_availability.last_changed) }} ago"
                " · coarse 0/50/100"
            ),
        },
    ],
}

HISTORY = {
    "type": "custom:mini-graph-card",
    "name": "Hot Water",
    "hours_to_show": 24,
    "points_per_hour": 4,
    "line_width": 2,
    "grid_options": {"columns": 12, "rows": "auto"},
    "entities": [
        {"entity": "sensor.water_heater_hot_water", "name": "Hot water %"},
        {
            "entity": "sensor.vue_garage_water_heater_power",
            "name": "Power",
            "y_axis": "secondary",
            "show_state": True,
            "color": "#ff9900",
        },
    ],
    "show": {"fill": "fade"},
}

NEW_CARDS = [GLANCE, CONTROL, HISTORY]
