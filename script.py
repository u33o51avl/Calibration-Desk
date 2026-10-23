import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_logs = []
_stdout_return = {}
_builtin_print = print
_result_file = None

_TOOL_KEY = "model-connect-r2025.2"
_AVLAST_HOME = r"C:\Program Files (x86)\AVL\R2025.2"
_AVL_PYTHON = Path(_AVLAST_HOME) / "user_python" / "python3" / "python.exe"


def log(msg: str):
    global _result_file
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    _builtin_print(line, file=sys.stderr)
    _logs.append(line)
    if _result_file is not None:
        with _result_file.open("a", encoding="utf-8") as f:
            print(line, file=f)


def parse_args() -> tuple[dict, dict]:
    argv = sys.argv[1:]
    args_file_path = None
    for i, arg in enumerate(argv):
        if arg == "--args-file" and i + 1 < len(argv):
            args_file_path = argv[i + 1]
            break

    if args_file_path is None:
        raise ValueError("--args-file argument missing")

    with open(args_file_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    params = {}
    params["parent-dump"] = raw.get("parent_dump", "")
    params["session-path"] = raw.get("session", {}).get("path", "")
    params["cache-path"] = raw.get("cache", {}).get("path", "")

    for paths_list in raw.get("dataset_paths", {}).values():
        if paths_list:
            params["dcm-file"] = paths_list[0]
            break

    params.update(raw.get("custom", {}))
    return params, raw


def write_result_file(argv: list, raw: dict, params: dict) -> Path:
    global _result_file
    parent_dump_path = Path(params["parent-dump"])
    result_dir = parent_dump_path.parent / "Result"
    result_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_path = result_dir / f"{timestamp}.txt"

    with output_path.open("w", encoding="utf-8") as f:
        f.write("=== ARGV ===\n")
        for arg in argv:
            f.write(f"  {arg}\n")
        f.write("\n")
        f.write("=== ARGS.JSON ===\n")
        f.write(json.dumps(raw, indent=2, ensure_ascii=False))
        f.write("\n\n")
        f.write("=== LOGS ===\n")

    _result_file = output_path
    log(f"Result file: {output_path}")
    return output_path


def dump_logs(output_path: Path):
    with output_path.open("a", encoding="utf-8") as f:
        f.write("\n=== STDOUT RETURN ===\n")
        f.write(json.dumps(_stdout_return, indent=2, ensure_ascii=False))
        f.write("\n")


def copy_repo(cache_path: str, session_path: str):
    src = Path(cache_path) / "repo"
    dst = Path(session_path) / "repo_copied_by_script"
    if not src.exists():
        log(f"Repo-Quelle nicht gefunden: {src}")
        return None
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    log(f"Repo kopiert: {src} -> {dst}")
    return dst


def init_submodules(repo_path: Path):
    result = subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        log(f"Submodule init: {result.stdout.strip()}")
    if result.stderr:
        log(f"Submodule init stderr: {result.stderr.strip()}")
    if result.returncode != 0:
        log(f"Submodule init fehlgeschlagen (exit={result.returncode})")
    else:
        log("Submodule init erfolgreich")


def copy_dcm(src_path: str, repo_path: Path):
    src = Path(src_path)
    if not src.exists():
        log(f"DCM source not found: {src}")
        return
    dst = (
        repo_path
        / "vPrototype_creta_showroom"
        / "vXCU" / "vADAS" / "vACC" / "03_SwBin"
        / "SwSys_AggregationUnitCalFile.dcm"
    )
    shutil.copy2(src, dst)
    log(f"DCM kopiert von:  {src.resolve()}")
    log(f"DCM kopiert nach: {dst.resolve()}")


def run_co_simulation(repo_path: Path):
    build_mc_dir = repo_path / "vPrototype_creta_showroom" / "_build" / "build_MC"
    env = os.environ.copy()
    env["AVLAST_HOME"] = _AVLAST_HOME
    cmd = [
        str(_AVL_PYTHON),
        r"dopapi\co_simulation.py",
        "--generateSilProject",
        "--targetProjectName", r"..\..\ARE4335.proj",
        "--sdvMetamodelFilePath", "avl_sdv_test.yml",
        "--SimStartTime", "0",
        "--SimEndTime", "60",
        "--SimTimeStep", "0.02",
        "--CouplingMechanism", "SEQUENTIAL",
        "--ResultOutputLevel", "COUPLING",
        "--ResultOutputCsv", "True",
        "--ResultOutputBinary", "True",
        "--ResultConvertMf4", "True",
        "--ResultOutputStepCount", "20",
    ]
    log(f"co_simulation.py starten in: {build_mc_dir}")
    result = subprocess.run(cmd, cwd=str(build_mc_dir), env=env, capture_output=True, text=True)
    if result.stdout:
        log(f"co_simulation stdout: {result.stdout.strip()}")
    if result.stderr:
        log(f"co_simulation stderr: {result.stderr.strip()}")
    log(f"co_simulation exit={result.returncode}")


def launch_model_connect(project_path: str):
    global _stdout_return
    _stdout_return = {
        "launch": {
            "tool": _TOOL_KEY,
            "args": [project_path],
            "cwd": str(Path(project_path).parent.resolve()),
        }
    }
    log(f"Launch: {_TOOL_KEY} {project_path}")


def launch_notepad(file_path: str):
    global _stdout_return
    _stdout_return = {
        "launch": {
            "tool": "notepad",
            "args": [file_path],
            "cwd": str(Path(file_path).parent.resolve()),
        }
    }
    log(f"Launch: notepad {file_path}")


def main(params: dict, output_path: Path):
    start_mc = params.get("start-model-connect", "false").lower() == "true"

    if not start_mc:
        log("start-model-connect deaktiviert -- Repo-Workflow wird uebersprungen.")
        launch_notepad(str(output_path))
        return

    cache_path = params.get("cache-path", "")
    session_path = params.get("session-path", "")

    if not cache_path or not session_path:
        log("cache-path oder session-path fehlt -- Repo-Workflow wird uebersprungen.")
        return

    repo_path = copy_repo(cache_path, session_path)
    if repo_path is None:
        return

    init_submodules(repo_path)

    dcm_path = params.get("dcm-file", "")
    if dcm_path:
        copy_dcm(dcm_path, repo_path)
    else:
        log("Kein dcm-file Parameter -- DCM wird nicht ersetzt.")

    run_co_simulation(repo_path)

    project_path = str(repo_path / "vPrototype_creta_showroom" / "ARE4335.proj")
    launch_model_connect(project_path)


if __name__ == "__main__":
    _params, _raw = parse_args()
    _output_path = write_result_file(sys.argv, _raw, _params)
    main(_params, _output_path)
    dump_logs(_output_path)
    _builtin_print(json.dumps(_stdout_return))
