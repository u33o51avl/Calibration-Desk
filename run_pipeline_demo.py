"""
run_pipeline_demo.py  —  lokale Alternative zur GitHub Actions Pipeline

Fuehrt exakt dieselben Schritte aus wie default-automated-sil-tests.yml,
aber ohne GitHub Actions und ohne smbclient. Das Netzlaufwerk muss erreichbar
sein (z. B. als gemountetes Laufwerk oder direkt als UNC-Pfad unter Windows).

JUnit-Report und PDF werden lokal im Unterordner _temp/<run-uuid>/ erzeugt
und danach als Attachments in CRETA hochgeladen.

Verwendung:
    python run_pipeline_demo.py
        --revision-suid   <SUID der Revision>
        --project-suid    <SUID des Projekts>
        --result-path     <Basisordner fuer Ergebnisse, z. B. //server/share/pipeline-results>
        --creta-username  <CRETA-Benutzername>  ]  eines
        --creta-password  <CRETA-Passwort>      ]  von beiden
        --token           <Bearer Token>         ]  angeben
        [--junit-fail-count N]   Anzahl JUnit-Tests, die fehlschlagen (Standard: 0)
        [--fail-sil]             Stage "Execute SiL Tests" schlaegt fehl
        [--fail-post]            Stage "Post-Processing" schlaegt fehl
        [--fail-upload]          Stage "Upload Report" schlaegt fehl

Abhaengigkeiten (einmalig installieren):
    pip install requests fpdf2 matplotlib
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "pipeline_src"))

import result_manager
import creta_api
from junit_creator import create_junit
from generate_pdf_report import parse_junit, build_pdf


TEST_CASES = [
    "ACC.TargetDetection.ACC_Target_Acquisition_StaticVehicle:passed",
    "ACC.FollowControl.ACC_FollowDistance_Level2_80kph:passed",
    "ACC.EmergencyResponse.ACC_BrakeToStop_CutIn_EmergencyDecel:passed",
    "ACC.DriverInteraction.ACC_ResumeAfterDriverOverride_Accelerator:passed",
    "ACC.SpeedControl.ACC_SpeedLimitAdaptation_TSR_Integration:passed",
    "ACC.StopAndGo.ACC_StopAndGo_DenseTraffic_Cycle:passed",
    "ACC.SpeedControl.ACC_CurveSpeedAdaptation_R150_Highway:passed",
    "ACC.SensorFusion.ACC_FusionDropout_RadarOnly_Fallback:passed",
    "ACC.TargetDetection.ACC_PrecedingVehicleLaneChange_TargetRelease:passed",
    "ACC.SpeedControl.ACC_SetSpeedRamp_0to130kph_GradientCompliance:passed",
]


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def parse_test_cases(raw_list: list, fail_count: int) -> list:
    import random
    cases = []
    for entry in raw_list:
        name_part, _, status = entry.rpartition(":")
        if not name_part:
            name_part, status = status, "passed"
        if "." in name_part:
            classname, name = name_part.rsplit(".", 1)
        else:
            classname, name = "", name_part
        cases.append({"name": name, "classname": classname, "status": status})

    if fail_count > 0:
        indices = random.sample(range(len(cases)), min(fail_count, len(cases)))
        for i in indices:
            cases[i]["status"] = "failed"

    return cases


def _sync_pipeline_run(token, pipeline_suid, result_path):
    with open(result_path) as f:
        data = json.load(f)
    creta_api.update_pipeline_run(token, pipeline_suid, data["Stages"], data["Status"])


def main():
    parser = argparse.ArgumentParser(
        description="Lokale Pipeline-Demo (entspricht default-automated-sil-tests.yml)"
    )
    parser.add_argument("--revision-suid",    required=True, help="SUID der Revision in CRETA")
    parser.add_argument("--project-suid",    required=True, help="SUID des Projekts in CRETA")
    parser.add_argument("--result-path",      required=True, help="Basisordner fuer Ergebnisse auf dem Netzlaufwerk")
    parser.add_argument("--creta-username",   help="CRETA-Benutzername (zusammen mit --creta-password)")
    parser.add_argument("--creta-password",   help="CRETA-Passwort (zusammen mit --creta-username)")
    parser.add_argument("--token",            help="Bearer Token (Alternative zu Username/Passwort)")
    parser.add_argument("--junit-fail-count", type=int, default=0, help="Anzahl fehlschlagender JUnit-Tests")
    parser.add_argument("--fail-sil",    action="store_true", help="Stage 'Execute SiL Tests' schlaegt fehl")
    parser.add_argument("--fail-post",   action="store_true", help="Stage 'Post-Processing' schlaegt fehl")
    parser.add_argument("--fail-upload", action="store_true", help="Stage 'Upload Report' schlaegt fehl")
    args = parser.parse_args()

    if args.token:
        pass
    elif args.creta_username and args.creta_password:
        pass
    else:
        parser.error("Entweder --token oder --creta-username + --creta-password angeben.")

    suid         = args.revision_suid
    project_suid = args.project_suid

    # ── CRETA-Token ──────────────────────────────────────────────────────────────
    if args.token:
        token = args.token
        log("Bearer Token wird verwendet.")
    else:
        log("Verbinde mit CRETA...")
        token = creta_api.connect(args.creta_username, args.creta_password)
        log("CRETA-Verbindung erfolgreich.")

    # ── Load Data ────────────────────────────────────────────────────────────────
    log("=== Stage: Load Data ===")

    job_url = f"local-demo/{suid}"

    log("Erstelle Pipeline Run in CRETA...")
    pr_response = creta_api.create_pipeline_run(
        token, project_suid, suid, "SiL Test Execution",
        "Execute the Sil Tests with a longer description.",
        job_url,
    )
    pipeline_suid = pr_response["Suid"]
    log(f"Pipeline Run erstellt: {pipeline_suid}")

    # result.json landet auf dem Netzlaufwerk
    run_dir     = Path(args.result_path) / suid / pipeline_suid
    result_path = run_dir / "result.json"
    run_dir.mkdir(parents=True, exist_ok=True)

    # JUnit und PDF werden lokal erzeugt, danach als Attachment hochgeladen
    temp_dir    = Path(__file__).parent / "_temp" / pipeline_suid
    temp_dir.mkdir(parents=True, exist_ok=True)
    junit_path  = temp_dir / "junit-report.xml"
    pdf_path    = temp_dir / "report.pdf"

    log(f"Ergebnisordner (Netzwerk): {run_dir}")
    log(f"Lokaler Temp-Ordner:       {temp_dir}")

    result_manager.create(
        str(result_path),
        "SiL Test Execution",
        "Execute the Sil Tests with a longer description.",
        job_url,
        args.creta_username,
    )

    creta_api.update_node_picture(token, suid, "Running")
    log("Node-Picture: Running")
    time.sleep(5)
    result_manager.update_stage(str(result_path), "Load Data", "Completed")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    log("Load Data: Completed")

    # ── Create SiL Environment ───────────────────────────────────────────────────
    log("=== Stage: Create SiL Environment ===")
    result_manager.update_stage(str(result_path), "Create SiL Environment", "Running")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    time.sleep(5)
    result_manager.update_stage(str(result_path), "Create SiL Environment", "Completed")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    log("Create SiL Environment: Completed")

    # ── Execute SiL Tests ────────────────────────────────────────────────────────
    log("=== Stage: Execute SiL Tests ===")
    result_manager.update_stage(str(result_path), "Execute SiL Tests", "Running")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    if args.fail_sil:
        result_manager.update_stage(str(result_path), "Execute SiL Tests", "Failed")
        _sync_pipeline_run(token, pipeline_suid, result_path)
        creta_api.update_node_picture(token, suid, "Failed")
        log("Execute SiL Tests: Failed — Pipeline abgebrochen.")
        sys.exit(1)
    result_manager.update_stage(str(result_path), "Execute SiL Tests", "Completed")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    log("Execute SiL Tests: Completed")

    # ── Post-Processing ──────────────────────────────────────────────────────────
    log("=== Stage: Post-Processing ===")
    result_manager.update_stage(str(result_path), "Post-Processing", "Running")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    if args.fail_post:
        result_manager.update_stage(str(result_path), "Post-Processing", "Failed")
        _sync_pipeline_run(token, pipeline_suid, result_path)
        creta_api.update_node_picture(token, suid, "Failed")
        log("Post-Processing: Failed — Pipeline abgebrochen.")
        sys.exit(1)

    log("Erstelle JUnit-Report...")
    test_cases = parse_test_cases(TEST_CASES, args.junit_fail_count)
    create_junit(str(junit_path), "SiL Tests", test_cases)

    log("Erstelle PDF-Report...")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    suite_name, cases, counts = parse_junit(str(junit_path))
    build_pdf(str(pdf_path), suite_name, cases, counts, generated_at)

    result_manager.update_stage(str(result_path), "Post-Processing", "Completed")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    log("Post-Processing: Completed")

    # ── Upload Report ────────────────────────────────────────────────────────────
    log("=== Stage: Upload Report ===")
    result_manager.update_stage(str(result_path), "Upload Report", "Running")
    _sync_pipeline_run(token, pipeline_suid, result_path)
    if args.fail_upload:
        result_manager.update_stage(str(result_path), "Upload Report", "Failed")
        _sync_pipeline_run(token, pipeline_suid, result_path)
        creta_api.update_node_picture(token, suid, "Failed")
        log("Upload Report: Failed — Pipeline abgebrochen.")
        sys.exit(1)

    log("Lade JUnit-Attachment hoch (Revision)...")
    creta_api.import_attachment(token, suid, str(junit_path), f"{pipeline_suid}_junit.xml")

    log("Lade PDF-Attachment hoch (Revision)...")
    creta_api.import_attachment(token, suid, str(pdf_path), f"{pipeline_suid}_report.pdf")

    log("Lade PDF-Attachment hoch (Pipeline Run)...")
    creta_api.import_attachment(token, pipeline_suid, str(pdf_path), "report.pdf")

    log("Importiere JUnit-Ergebnisse (Pipeline Run)...")
    creta_api.import_junit(token, pipeline_suid, project_suid, str(junit_path))

    result_manager.update_stage(str(result_path), "Upload Report", "Completed")

    if args.junit_fail_count > 0:
        result_manager.set_overall_status(str(result_path), "Failed")
        creta_api.update_node_picture(token, suid, "Failed")
        log("Node-Picture: Failed (JUnit-Fehler)")
    else:
        creta_api.update_node_picture(token, suid, "Completed")
        log("Node-Picture: Completed")

    _sync_pipeline_run(token, pipeline_suid, result_path)

    log(f"Pipeline abgeschlossen.")
    log(f"  Netzwerk:  {run_dir}")
    log(f"  Lokal:     {temp_dir}")


if __name__ == "__main__":
    main()
