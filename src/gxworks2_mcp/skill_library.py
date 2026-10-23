"""三项工程技能按需读取；不访问调用者提供的路径，不执行技能附带代码。"""
import hashlib
from pathlib import Path

_ROOT=Path(__file__).resolve().parent/'data'/'skills'
_SKILLS={
    'mitsubishi-io-planner':('三菱软元件与标签规划','新增或修改地址规划时核对占用、隐含多字范围、保留区及接线依据'),
    'mitsubishi-mode-review':('三菱模式与报警审查','审查手动/自动、暂停/停止、报警锁存及复位的优先级和输出控制'),
    'mitsubishi-scenario-tests':('三菱工艺场景验收','将明确的工艺要求转换为正常、异常及恢复场景，复用真实仿真批量验收'),
}


def list_skills():
    """目录仅返回元数据，避免加载无关技能正文。"""
    return [dict(name=name,title=title,description=description,version='1.0.0')
            for name,(title,description) in _SKILLS.items()]


def read_skill(name):
    if not isinstance(name,str) or name not in _SKILLS:
        raise ValueError('未知工程技能；使用 gx_skill_list 查询可用名称')
    path=(_ROOT/name/'SKILL.md').resolve()
    if not path.is_relative_to(_ROOT):raise ValueError('技能路径越界')
    content=path.read_text(encoding='utf-8')
    if len(content)>16000:raise ValueError('技能正文超过按需读取上限')
    return dict(name=name,version='1.0.0',content=content,
                sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
                role='工程指导；程序仍需原生编译、读回及适用范围内的验收')
