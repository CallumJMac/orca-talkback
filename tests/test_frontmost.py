import subprocess

from orca_talkback.frontmost import frontmost_app_name, orca_is_frontmost


def fake_run(outputs: dict[str, str], calls: list | None = None):
    def run(command, **options):
        if calls is not None:
            calls.append(command)
        if "missing" in outputs:
            raise FileNotFoundError("lsappinfo")
        return subprocess.CompletedProcess(command, 0, stdout=outputs[command[1]], stderr="")
    return run


def test_reads_the_frontmost_app_name():
    run = fake_run({"front": "ASN:0x0-0x12345:\n", "info": '"LSDisplayName"="Orca"\n'})
    assert frontmost_app_name(run) == "Orca"


def test_orca_in_front():
    assert orca_is_frontmost(fake_run({"front": "ASN:0x0-0x1:", "info": '"LSDisplayName"="Orca"'}))


def test_other_app_in_front():
    assert not orca_is_frontmost(fake_run({"front": "ASN:0x0-0x2:", "info": '"LSDisplayName"="Google Chrome"'}))


def test_unknown_when_lsappinfo_is_missing_does_not_block_speech():
    assert frontmost_app_name(fake_run({"missing": ""})) is None
    assert orca_is_frontmost(fake_run({"missing": ""}))


def test_asks_lsappinfo_for_the_front_app_then_its_name():
    calls: list = []
    frontmost_app_name(fake_run({"front": "ASN:0x0-0x3:", "info": '"LSDisplayName"="Orca"'}, calls))
    assert calls == [["lsappinfo", "front"], ["lsappinfo", "info", "-only", "name", "ASN:0x0-0x3:"]]
