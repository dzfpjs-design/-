"""Versioned user-defined platform policy, not report evidence or authority.

Callers must bind this exact value to the independent revision request.  The
policy never renews source permission, approves a candidate or invokes a tool.
"""
import copy


SCHEMA = 'report-business-flow-platform-policy-v185'
PLATFORM_POLICY_SCHEMA = SCHEMA
VERSION = 1

_POLICY = {
    'schema': SCHEMA,
    'version': VERSION,
    'authority': 'user_defined_platform_architecture',
    'evidence_policy': (
        '本政策是用户确定的产品架构，不是报告事实，也不是执行授权。报告和旧候选均为待核输入，'
        '不能用文档称谓、列举顺序或旧分工覆盖本政策；不能从未发送范围补事实。'
        '架构映射来自本政策时应明确说明，不能伪装成报告原文明示或编造原文引用。'),
    'roles': {
        'business_agent': (
            '领域实例唯一业务大脑，负责目标、业务判断、优先级、规划、跨业务协调、委派、重规划和结果核验。'
            '多个动态业务共享该中枢；不得代替被授权人的批准、指挥及法定判断。'),
        'perception_agent': (
            '负责专业提取、质量与时空边界核查、多源融合、对象状态理解、异常及感知缺口发现；保留未知、过期与冲突。'
            '异常可靠上报业务大脑，原件与分析交数据管理。仅在既定授权及父目标、设备、时段、预算和停止条件内自主补采，'
            '越界或失败升级协调，不得扩权或把无数据当正常。'),
        'data_agent': (
            '依业务和感知需求形成数据方案，保存原件、事实、推断、版本和血缘，主动质检治理、组织知识和索引，'
            '提供受权限约束的事实、历史、记忆、有据查询及充分性缺口。不是跨业务总指挥，也不只是被动归档；'
            '修正遵守可逆性及授权边界。'),
        'task_center': '负责待办分配、状态、审批流转、通知和回执，不自行制定业务策略或生成权限。',
        'execution_service': '负责已批准动作的确定性调度、接口调用、幂等和执行回执，不自行制定业务策略或生成权限。',
        'human': '承担必要核对、批准、例外处置、指挥和独立验收；岗位名称不等于账号权限。',
    },
    'planning_rules': [
        '按实际职责映射actor并保留原文称谓与出处；不要求每条流程凑齐三智能体，不为满足架构编造步骤。责任冲突或无法无损映射进入questions。',
        '章节、功能、职责列举不自动代表先后。仅明示时序、因果、前置条件或明确设计推断才能建立顺序、条件、并行及汇合；不同触发分开规划，区分持续服务和一次有限处理。',
        '达到既定上报条件后，异常通知不得等待无关补核、每日整理或后续处置审批；补核可并行并在新证据后更新判断。保留原文必要前置条件，不把送达当处置完成。',
        '触发、输入证据、动作和产出须因果一致。未来步骤才产生的证据不能当作启动时已具备；同一事件不能既是已发生触发，又由本流程后续步骤首次产生。未知顺序、条件和终止依据保留问题，不能强连。',
    ],
    'revision_rules': [
        '先独立审读本次原来源和既有计划，再逐项说明职责、顺序、条件及证据问题；不能照抄旧计划或以凑齐actor代替判断。',
        '只生成独立修订候选，不改原答、费用或人工记录。原文明示与推断分别说明，不能以平台政策补造业务事实。',
        '结构通过、平台补quote和修订完成均不证明业务语义、整报告覆盖或采用、发布、执行许可。',
    ],
}


def platform_policy():
    """A detached exact policy value; neither a registry lookup nor a grant."""
    return copy.deepcopy(_POLICY)
