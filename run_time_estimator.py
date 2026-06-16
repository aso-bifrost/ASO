"""
Run Time Estimator
==================
Estimates total cycle time broken down into fluidics and imaging components.
Add new entries to VERSIONS to track improvements over time.
Each version gets a timestamp (date string) and a full parameter set.
Run this script to generate an interactive HTML area chart.

Usage:
    python run_time_estimator.py
    -> outputs run_time_chart.html in the same directory
"""

from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG: Edit parameters here. Add a new block to VERSIONS for each change.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FluidicsStep:
    """One fluidic step: reagent name, repeat count, volumes, speeds, wait time."""
    name: str
    reagent: str
    repeats: int
    volume_ul: float           # per well per repeat (used for dispense calc)
    aspirate_duration_s: float # fixed aspirate duration per well (set as time, not flow rate)
    dispense_speed_ul_s: float # dispense flow rate in ul/s (volume / speed = dispense time)
    wait_min: float            # incubation wait after dispense (starts per-well)
    in_cycle_0: bool           # True = included in cycle 0, False = skipped in cycle 0


@dataclass
class ImagingParams:
    """Imaging parameters."""
    objective: str                       # "4x" or "10x"
    fovs_per_well: int                   # 475 for 10x, 75 for 4x
    channels: list[str]                  # e.g. ["405", "561", "638"]
    exposure_ms: list[float]             # matching channel exposures
    simultaneous_pairs: list[list[str]]  # channels acquired simultaneously
    fov_move_ms: float                   # confirmed 560ms from plan CSV
    camera_overhead_ms: float            # readout + LED + leds_off per frame
                                         # confirmed 580ms from runs
                                         # applies to every frame: z-map slices AND regular FOVs
                                         # update this when PWM trigger mode is implemented

    # ── Shared per-FOV overhead (same for z-map and acquisition) ──
    set_wheels_ms: float                 # confirmed 51ms from plan CSV
    set_led_ms: float                    # confirmed 26ms from plan CSV
    end_stack_ms: float                  # confirmed 23ms from plan CSV (variable, using mean)

    # ── Z-mapping ──
    # z_map_duration_per_well_s is the ground truth from the z-map JSON total_duration_s.
    # The breakdown fields below are informational — they describe what drives the duration.
    # When you have enough data to fully reconstruct the total from parts,
    # delete z_map_duration_per_well_s and compute it from the breakdown instead.
    z_map_duration_per_well_s: float     # from z-map JSON total_duration_s (e.g. 354.8)
    z_map_points_per_well: int           # number of z-map points per well (e.g. 33)
    z_map_slices_per_point: int          # number of z slices per point (e.g. 11)
    z_map_exposure_ms: float             # exposure per z-map slice in ms (e.g. 25ms for 405)
    z_map_move_ms: float                 # informational: mean move time between z-map points
                                         # includes coarse xy move + z-moves between slices
                                         # derived: (total_per_point - slices*slice_ms - overhead)


@dataclass
class InstrumentParams:
    """Instrument-level timing parameters."""
    needle_insert_s: float   # seconds per well
    needle_retract_s: float  # seconds per well
    wells: int               # number of wells in the run


@dataclass
class VersionConfig:
    """A full parameter snapshot at a point in time."""
    date: str                          # "YYYY-MM-DD" — x-axis label
    label: str                         # short description shown on chart
    cycles: int                        # total number of imaging cycles
    fluidics: list[FluidicsStep]
    imaging: ImagingParams
    instrument: InstrumentParams
    notes: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# SHARED FLUIDICS — define once, reuse across versions with the same protocol
# ─────────────────────────────────────────────────────────────────────────────

FLUIDICS_LEE = [
    FluidicsStep("JCM cleave",    "JCM",     repeats=1, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=6.0,  in_cycle_0=False),
    FluidicsStep("MB2 wash fast", "MB2",     repeats=3, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=0.1,  in_cycle_0=False),
    FluidicsStep("MB2 wash slow", "MB2",     repeats=3, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=2.0,  in_cycle_0=False),
    FluidicsStep("JIM inc",       "JIM",     repeats=1, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=15.0, in_cycle_0=True),
    FluidicsStep("MB2 wash x5",   "MB2",     repeats=5, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=0.1,  in_cycle_0=True),
    FluidicsStep("MB2 wash x4",   "MB2",     repeats=4, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=5.0,  in_cycle_0=True),
    FluidicsStep("Imaging buf",   "imaging", repeats=1, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=100, wait_min=0.1,  in_cycle_0=True),
]

FLUIDICS_KNF = [
    FluidicsStep("JCM cleave",    "JCM",     repeats=1, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=6.0,  in_cycle_0=False),
    FluidicsStep("MB2 wash fast", "MB2",     repeats=3, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=0.1,  in_cycle_0=False),
    FluidicsStep("MB2 wash slow", "MB2",     repeats=3, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=2.0,  in_cycle_0=False),
    FluidicsStep("JIM inc",       "JIM",     repeats=1, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=15.0, in_cycle_0=True),
    FluidicsStep("MB2 wash x5",   "MB2",     repeats=5, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=0.1,  in_cycle_0=True),
    FluidicsStep("MB2 wash x4",   "MB2",     repeats=4, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=5.0,  in_cycle_0=True),
    FluidicsStep("Imaging buf",   "imaging", repeats=1, volume_ul=650, aspirate_duration_s=12, dispense_speed_ul_s=300, wait_min=0.1,  in_cycle_0=True),
]


# ─────────────────────────────────────────────────────────────────────────────
# SHARED IMAGING PARAMS — define base configs, override per version as needed
# ─────────────────────────────────────────────────────────────────────────────

IMAGING_10X = ImagingParams(
    objective="10x",
    fovs_per_well=475,
    channels=["405", "561", "638"],
    exposure_ms=[25.0, 300.0, 300.0],
    simultaneous_pairs=[["561", "638"]],
    fov_move_ms=560.0,
    camera_overhead_ms=580.0,
    set_wheels_ms=51.0,
    set_led_ms=26.0,
    end_stack_ms=23.0,
    z_map_duration_per_well_s=322.5, #354.8 from JSON file /11 * 10, for 10 slices
    z_map_points_per_well=33,
    z_map_slices_per_point=10,
    z_map_exposure_ms=25.0,
    z_map_move_ms=2554.0,
)

IMAGING_4X = ImagingParams(
    objective="4x",
    fovs_per_well=75,
    channels=["405", "561", "638"],
    exposure_ms=[25.0, 300.0, 300.0],
    simultaneous_pairs=[["561", "638"]],
    fov_move_ms=560.0,              # likely similar stage speed
    camera_overhead_ms=580.0,       # same camera hardware
    set_wheels_ms=51.0,
    set_led_ms=26.0,
    end_stack_ms=23.0,
    z_map_duration_per_well_s=56.0, # estimate: 75 * 0.07 ≈ 5 points, scale from 10x
    z_map_points_per_well=5,        # 7% of 75 FOVs ≈ 5 points
    z_map_slices_per_point=10,
    z_map_exposure_ms=25.0,
    z_map_move_ms=2554.0,           # placeholder — update when measured
)


# ─────────────────────────────────────────────────────────────────────────────
# VERSIONS — add new entries here as the protocol/instrument changes
# ─────────────────────────────────────────────────────────────────────────────

VERSIONS: list[VersionConfig] = [

    # ── 10x versions ──

    VersionConfig(
        date="2026-04-07",
        label="Baseline (Lee pumps)",
        notes="Baseline 6W Lee pumps, 10x objective.",
        cycles=12,
        instrument=InstrumentParams(needle_insert_s=1.6, needle_retract_s=1.6, wells=6),
        fluidics=FLUIDICS_LEE,
        imaging=IMAGING_10X,
    ),

    VersionConfig(
        date="2026-06-10",
        label="KNF pump switch",
        notes="Switched to KNF pumps, 6W, 10x.",
        cycles=12,
        instrument=InstrumentParams(needle_insert_s=1.6, needle_retract_s=1.6, wells=6),
        fluidics=FLUIDICS_KNF,
        imaging=IMAGING_10X,
    ),

    # ── 4x versions ──

    VersionConfig(
        date="2026-04-07",
        label="Baseline (Lee pumps)",
        notes="Baseline 6W Lee pumps, 4x objective.",
        cycles=12,
        instrument=InstrumentParams(needle_insert_s=1.6, needle_retract_s=1.6, wells=6),
        fluidics=FLUIDICS_LEE,
        imaging=IMAGING_4X,
    ),

    VersionConfig(
        date="2026-06-10",
        label="KNF pump switch",
        notes="Switched to KNF pumps, 6W, 4x.",
        cycles=12,
        instrument=InstrumentParams(needle_insert_s=1.6, needle_retract_s=1.6, wells=6),
        fluidics=FLUIDICS_KNF,
        imaging=IMAGING_4X,
    ),

    # ── ADD NEW VERSIONS BELOW ──
    # VersionConfig(
    #     date="2026-07-01",
    #     label="PWM trigger mode",
    #     notes="Camera overhead reduced via PWM trigger mode.",
    #     cycles=12,
    #     instrument=InstrumentParams(needle_insert_s=1.6, needle_retract_s=1.6, wells=6),
    #     fluidics=FLUIDICS_KNF,
    #     imaging=ImagingParams(
    #         ...IMAGING_10X fields...
    #         camera_overhead_ms=150.0,          # <-- update after PWM fix
    #         z_map_duration_per_well_s=????,     # <-- re-run z-map and paste total_duration_s
    #     ),
    # ),

]


# ─────────────────────────────────────────────────────────────────────────────
# CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

def fluidics_step_time_min(step: FluidicsStep, instrument: InstrumentParams) -> dict:
    """Returns pump_min and wait_min for this step."""
    wells = instrument.wells
    per_well_s = (
        instrument.needle_insert_s
        + step.aspirate_duration_s
        + (step.volume_ul / step.dispense_speed_ul_s)
        + instrument.needle_retract_s
    )
    pump_total_min = (per_well_s * wells * step.repeats) / 60.0
    wait_total_min = step.wait_min * step.repeats
    return {
        "pump_min": pump_total_min,
        "wait_min": wait_total_min,
    }


def imaging_time_min(imaging: ImagingParams, wells: int) -> dict:
    """Returns z_map_min and acquisition_min."""

    # ── Z-mapping ──────────────────────────────────────────────────────────
    z_map_min = (imaging.z_map_duration_per_well_s * wells) / 60.0

    # ── Acquisition ────────────────────────────────────────────────────────
    overhead_ms = imaging.set_wheels_ms + imaging.set_led_ms + imaging.end_stack_ms

    simultaneous_ms = max(
        imaging.exposure_ms[i]
        for i, ch in enumerate(imaging.channels)
        if any(ch in pair for pair in imaging.simultaneous_pairs)
    ) + imaging.camera_overhead_ms

    sequential_ms = sum(
        imaging.exposure_ms[i] + imaging.camera_overhead_ms
        for i, ch in enumerate(imaging.channels)
        if not any(ch in pair for pair in imaging.simultaneous_pairs)
    )

    time_per_fov_ms = (
        imaging.fov_move_ms
        + simultaneous_ms
        + sequential_ms
        + overhead_ms
    )
    acq_ms = imaging.fovs_per_well * time_per_fov_ms * wells
    acq_min = acq_ms / 60_000.0

    return {
        "z_map_min": z_map_min,
        "acq_min": acq_min,
        "z_map_points_per_well": imaging.z_map_points_per_well,
        "acq_fovs_per_well": imaging.fovs_per_well,
        "time_per_fov_ms": round(time_per_fov_ms, 1),
    }


def compute_cycle_breakdown(config: VersionConfig, cycle_index: int) -> dict:
    is_first_cycle = (cycle_index == 0)
    fluidics_pump = 0.0
    fluidics_wait = 0.0
    for step in config.fluidics:
        if is_first_cycle and not step.in_cycle_0:
            continue
        times = fluidics_step_time_min(step, config.instrument)
        fluidics_pump += times["pump_min"]
        fluidics_wait += times["wait_min"]
    img = imaging_time_min(config.imaging, config.instrument.wells)
    return {
        "fluidics_pump": fluidics_pump,
        "fluidics_wait": fluidics_wait,
        "z_mapping": img["z_map_min"],
        "acquisition": img["acq_min"],
        "total": fluidics_pump + fluidics_wait + img["z_map_min"] + img["acq_min"],
        "z_map_points_per_well": img["z_map_points_per_well"],
        "acq_fovs_per_well": img["acq_fovs_per_well"],
        "time_per_fov_ms": img["time_per_fov_ms"],
    }


def compute_version_summary(config: VersionConfig) -> dict:
    breakdowns = [compute_cycle_breakdown(config, i) for i in range(config.cycles)]
    keys = ["fluidics_pump", "fluidics_wait", "z_mapping", "acquisition", "total"]
    mean = {k: sum(b[k] for b in breakdowns) / len(breakdowns) for k in keys}
    mean["total_run_min"] = sum(b["total"] for b in breakdowns)
    mean["per_cycle_breakdowns"] = breakdowns
    return mean


# ─────────────────────────────────────────────────────────────────────────────
# HTML CHART GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_html(versions: list[VersionConfig], output_path: Path) -> None:
    # Group versions by objective
    objectives = sorted(set(v.imaging.objective for v in versions))

    all_data = {}
    for obj in objectives:
        obj_versions = [v for v in versions if v.imaging.objective == obj]
        summaries = [compute_version_summary(v) for v in obj_versions]

        all_data[obj] = {
            "versions": [{
                "date": v.date,
                "label": v.label,
                "notes": v.notes,
                "cycles": v.cycles,
                "objective": v.imaging.objective,
                "wells": v.instrument.wells,
                "fovs_per_well": v.imaging.fovs_per_well,
            } for v in obj_versions],
            "fp": [round(s["fluidics_pump"], 2) for s in summaries],
            "fw": [round(s["fluidics_wait"], 2) for s in summaries],
            "zm": [round(s["z_mapping"], 2) for s in summaries],
            "aq": [round(s["acquisition"], 2) for s in summaries],
            "totals": [round(s["total"], 2) for s in summaries],
            "run_totals": [round(s["total_run_min"] / 60, 2) for s in summaries],
            "per_cycle": [{
                "label": v.label,
                "date": v.date,
                "cycles": [{
                    "cycle": i,
                    "fluidics_pump": round(b["fluidics_pump"], 2),
                    "fluidics_wait": round(b["fluidics_wait"], 2),
                    "z_mapping": round(b["z_mapping"], 2),
                    "acquisition": round(b["acquisition"], 2),
                    "total": round(b["total"], 2),
                } for i, b in enumerate(s["per_cycle_breakdowns"])]
            } for v, s in zip(obj_versions, summaries)],
        }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Run Time Estimator</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f8f8f6; color: #1a1a1a; padding: 2rem; }}
  h1 {{ font-size: 20px; font-weight: 500; margin-bottom: 4px; }}
  .subtitle {{ font-size: 13px; color: #666; margin-bottom: 1.5rem; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 1.5rem; font-size: 12px; color: #555; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-swatch {{ width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }}
  .chart-wrap {{ background: white; border-radius: 12px; border: 0.5px solid #e0e0d8;
                 padding: 1.5rem; margin-bottom: 1.5rem; }}
  .chart-title {{ font-size: 14px; font-weight: 500; margin-bottom: 1rem; color: #333; }}
  .btn-row {{ display: flex; gap: 8px; margin-bottom: 1rem; }}
  button {{ font-size: 12px; padding: 6px 14px; border: 0.5px solid #ccc; border-radius: 6px;
            background: white; cursor: pointer; color: #333; transition: background 0.15s; }}
  button.active {{ background: #1a1a1a; color: white; border-color: #1a1a1a; }}
  button:hover:not(.active) {{ background: #f0f0ee; }}
  .obj-badge {{ display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px;
                border-radius: 4px; background: #e8f4fd; color: #2a7ab5; margin-left: 8px;
                vertical-align: middle; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                  gap: 12px; margin-bottom: 1.5rem; }}
  .stat {{ background: white; border-radius: 8px; border: 0.5px solid #e0e0d8; padding: 1rem; }}
  .stat-label {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
  .stat-value {{ font-size: 22px; font-weight: 500; }}
  .notes-wrap {{ font-size: 12px; color: #888; margin-top: 8px; font-style: italic; }}
  #drilldown-wrap {{ display: none; }}
  #drilldown-wrap.visible {{ display: block; }}
  .back-btn {{ font-size: 12px; color: #378ADD; cursor: pointer; text-decoration: underline;
               margin-bottom: 1rem; display: inline-block; background: none; border: none; padding: 0; }}
</style>
</head>
<body>

<h1>Instrument run time estimator <span class="obj-badge" id="obj-badge">10x</span></h1>
<p class="subtitle">Mean time per cycle across all versions. Click a data point to drill into per-cycle breakdown.</p>

<div class="legend">
  <span class="legend-item"><span class="legend-swatch" style="background:#D4537E"></span>Fluidics — pump (aspirate/dispense)</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#BA7517"></span>Fluidics — wait (incubation)</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#1D9E75"></span>Z-mapping</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#378ADD"></span>Acquisition</span>
</div>

<div class="btn-row">
  <div id="obj-btns" style="display:flex;gap:8px;margin-right:16px;"></div>
  <div style="display:flex;gap:8px;">
    <button id="btn-mean" class="active" onclick="showMode('mean')">Mean per cycle</button>
    <button id="btn-total" onclick="showMode('total')">Total run time</button>
  </div>
</div>

<div class="chart-wrap" id="main-chart-wrap">
  <div class="chart-title" id="main-chart-title">Mean cycle time breakdown by version</div>
  <div style="position:relative;width:100%;height:360px;">
    <canvas id="mainChart" role="img"
      aria-label="Stacked area chart showing mean cycle time broken down into fluidics pump, fluidics wait, z-mapping, and acquisition across instrument versions over time.">
    </canvas>
  </div>
  <p class="notes-wrap" id="hover-notes">Hover a point to see version notes. Click to drill into per-cycle breakdown.</p>
</div>

<div class="stats-grid" id="stats-grid"></div>

<div id="drilldown-wrap" class="chart-wrap">
  <button class="back-btn" onclick="closeDrilldown()">← back to overview</button>
  <div class="chart-title" id="drill-title">Per-cycle breakdown</div>
  <div style="position:relative;width:100%;height:360px;">
    <canvas id="drillChart" role="img" aria-label="Per-cycle stacked bar chart for selected version."></canvas>
  </div>
</div>

<script>
const ALL_DATA = {json.dumps(all_data)};
const OBJECTIVES = {json.dumps(objectives)};
const COLORS = {{ fp: '#D4537E', fw: '#BA7517', zm: '#1D9E75', aq: '#378ADD' }};

let currentObjective = OBJECTIVES[0];
let currentMode = 'mean';
let mainChart = null;
let drillChart = null;

function D() {{ return ALL_DATA[currentObjective]; }}

// Build objective buttons
const objContainer = document.getElementById('obj-btns');
OBJECTIVES.forEach(obj => {{
  const btn = document.createElement('button');
  btn.textContent = obj;
  btn.id = 'btn-obj-' + obj;
  btn.onclick = () => switchObjective(obj);
  if (obj === currentObjective) btn.classList.add('active');
  objContainer.appendChild(btn);
}});

function switchObjective(obj) {{
  currentObjective = obj;
  OBJECTIVES.forEach(o => {{
    document.getElementById('btn-obj-' + o).classList.toggle('active', o === obj);
  }});
  document.getElementById('obj-badge').textContent = obj;
  closeDrilldown();
  buildMainChart();
  updateStats(D().versions.length - 1);
}}

function makeDatasets(mode) {{
  const d = D();
  const labels = d.versions.map(v => v.date + '\\n' + v.label);
  if (mode === 'mean') {{
    return {{
      labels,
      datasets: [
        {{ label: 'Fluidics — pump', data: d.fp, backgroundColor: COLORS.fp+'cc', borderColor: COLORS.fp, borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 5, pointHoverRadius: 8 }},
        {{ label: 'Fluidics — wait', data: d.fw, backgroundColor: COLORS.fw+'cc', borderColor: COLORS.fw, borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 5, pointHoverRadius: 8 }},
        {{ label: 'Z-mapping',       data: d.zm, backgroundColor: COLORS.zm+'cc', borderColor: COLORS.zm, borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 5, pointHoverRadius: 8 }},
        {{ label: 'Acquisition',     data: d.aq, backgroundColor: COLORS.aq+'cc', borderColor: COLORS.aq, borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 5, pointHoverRadius: 8 }},
      ]
    }};
  }} else {{
    return {{
      labels,
      datasets: [
        {{ label: 'Total run time (hrs)', data: d.run_totals, backgroundColor: '#7F77DD'+'cc', borderColor: '#7F77DD', borderWidth: 2, fill: true, tension: 0.3, pointRadius: 5, pointHoverRadius: 8 }},
      ]
    }};
  }}
}}

function buildMainChart() {{
  const ctx = document.getElementById('mainChart').getContext('2d');
  if (mainChart) mainChart.destroy();
  const chartData = makeDatasets(currentMode);
  mainChart = new Chart(ctx, {{
    type: 'line',
    data: chartData,
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            title: (items) => {{
              const v = D().versions[items[0].dataIndex];
              return v.date + ' — ' + v.label;
            }},
            footer: (items) => {{
              if (currentMode === 'mean') {{
                const total = items.reduce((s, i) => s + i.parsed.y, 0);
                return 'Mean cycle total: ' + total.toFixed(1) + ' min\\nClick to see per-cycle detail';
              }}
              return 'Click to see per-cycle detail';
            }}
          }}
        }}
      }},
      onClick: (evt, elements) => {{ if (elements.length > 0) openDrilldown(elements[0].index); }},
      onHover: (evt, elements) => {{
        if (elements.length > 0) {{
          const i = elements[0].index;
          document.getElementById('hover-notes').textContent = D().versions[i].notes || '';
          updateStats(i);
        }}
      }},
      scales: {{
        x: {{ stacked: currentMode === 'mean', ticks: {{ font: {{ size: 11 }}, maxRotation: 30 }}, grid: {{ display: false }} }},
        y: {{
          stacked: currentMode === 'mean',
          title: {{ display: true, text: currentMode === 'mean' ? 'minutes per cycle' : 'total run hours', font: {{ size: 12 }} }},
          ticks: {{ callback: v => currentMode === 'mean' ? v + ' min' : v + ' hr' }}
        }}
      }}
    }}
  }});
  document.getElementById('main-chart-title').textContent =
    currentMode === 'mean'
      ? 'Mean cycle time breakdown by version (' + currentObjective + ')'
      : 'Total run time by version (' + currentObjective + ')';
}}

function updateStats(i) {{
  const d = D();
  const v = d.versions[i];
  const total = d.totals[i];
  const fl = (d.fp[i] + d.fw[i]).toFixed(1);
  const img = (d.zm[i] + d.aq[i]).toFixed(1);
  document.getElementById('stats-grid').innerHTML = `
    <div class="stat"><div class="stat-label">Version</div><div class="stat-value" style="font-size:15px">${{v.label}}</div></div>
    <div class="stat"><div class="stat-label">Mean cycle time</div><div class="stat-value">${{total.toFixed(1)}} min</div></div>
    <div class="stat"><div class="stat-label">Fluidics total</div><div class="stat-value">${{fl}} min</div></div>
    <div class="stat"><div class="stat-label">Imaging total</div><div class="stat-value">${{img}} min</div></div>
    <div class="stat"><div class="stat-label">Total run time</div><div class="stat-value">${{d.run_totals[i].toFixed(1)}} hr</div></div>
    <div class="stat"><div class="stat-label">Objective / Wells / FOVs</div><div class="stat-value" style="font-size:15px">${{v.objective}} / ${{v.wells}}W / ${{v.fovs_per_well}} FOV</div></div>
  `;
}}

function showMode(mode) {{
  currentMode = mode;
  document.getElementById('btn-mean').classList.toggle('active', mode === 'mean');
  document.getElementById('btn-total').classList.toggle('active', mode === 'total');
  buildMainChart();
}}

function openDrilldown(versionIndex) {{
  const data = D().per_cycle[versionIndex];
  document.getElementById('drilldown-wrap').classList.add('visible');
  document.getElementById('drill-title').textContent =
    'Per-cycle breakdown — ' + data.date + ' — ' + data.label + ' (' + currentObjective + ')';
  const cycleLabels = data.cycles.map(c => 'Cycle ' + c.cycle);
  const ctx = document.getElementById('drillChart').getContext('2d');
  if (drillChart) drillChart.destroy();
  drillChart = new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: cycleLabels,
      datasets: [
        {{ label: 'Fluidics — pump', data: data.cycles.map(c => c.fluidics_pump), backgroundColor: COLORS.fp, borderWidth: 0 }},
        {{ label: 'Fluidics — wait', data: data.cycles.map(c => c.fluidics_wait), backgroundColor: COLORS.fw, borderWidth: 0 }},
        {{ label: 'Z-mapping',       data: data.cycles.map(c => c.z_mapping),     backgroundColor: COLORS.zm, borderWidth: 0 }},
        {{ label: 'Acquisition',     data: data.cycles.map(c => c.acquisition),   backgroundColor: COLORS.aq, borderWidth: 0 }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ footer: (items) => 'Total: ' + items.reduce((s,i) => s+i.parsed.y, 0).toFixed(1) + ' min' }} }}
      }},
      scales: {{
        x: {{ stacked: true, grid: {{ display: false }} }},
        y: {{ stacked: true, ticks: {{ callback: v => v + ' min' }} }}
      }}
    }}
  }});
  document.getElementById('drilldown-wrap').scrollIntoView({{ behavior: 'smooth' }});
}}

function closeDrilldown() {{
  document.getElementById('drilldown-wrap').classList.remove('visible');
}}

buildMainChart();
updateStats(D().versions.length - 1);
</script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    print(f"Chart written to: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    output = Path(__file__).parent / "run_time_chart.html"
    generate_html(VERSIONS, output)

    print("\n── Summary ──")
    for v in VERSIONS:
        s = compute_version_summary(v)
        print(f"\n{v.date} — {v.label} ({v.imaging.objective})")
        print(f"  Mean cycle time : {s['total']:.1f} min")
        print(f"  Fluidics pump   : {s['fluidics_pump']:.1f} min")
        print(f"  Fluidics wait   : {s['fluidics_wait']:.1f} min")
        print(f"  Z-mapping       : {s['z_mapping']:.1f} min")
        print(f"  Acquisition     : {s['acquisition']:.1f} min")
        print(f"  Total run time  : {s['total_run_min']/60:.1f} hr")