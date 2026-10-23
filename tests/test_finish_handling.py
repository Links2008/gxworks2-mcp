"""交付入口不能把文件变更、跳过测试或编译失败判为通过。"""
import hashlib
import pytest
from tools.finish_handling_sfc import verify_native, verify_tests


def test_modified_project_rejected(tmp_path):
    p = tmp_path/'a.gxw'; p.write_bytes(b'changed')
    report = dict(sha256=hashlib.sha256(b'original').hexdigest(), compiledReadback=True,
                  nativeProjectCompile=dict(compileStatus='accepted',checkStatus='accepted',errors=0,warnings=0))
    with pytest.raises(ValueError): verify_native(p, report)


@pytest.mark.parametrize('attributes', ['tests="0"', 'tests="9" skipped="1"', 'tests="9" failures="1"', 'tests="9" errors="1"'])
def test_incomplete_tests_rejected(tmp_path, attributes):
    p = tmp_path/'tests.xml'; p.write_text(f'<testsuites><testsuite {attributes}/></testsuites>')
    with pytest.raises(ValueError): verify_tests(p)


def test_complete_tests_accepted(tmp_path):
    p = tmp_path/'tests.xml'; p.write_text('<testsuites><testsuite tests="9" failures="0" errors="0" skipped="0"/></testsuites>')
    assert verify_tests(p) == 9
