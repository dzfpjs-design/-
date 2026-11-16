import argparse
import getpass
from uuid import UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from .db import engine, migrate
from .bootstrap import create_administrator
from .security import password_hash


def job_uuid(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError("任务编号须为标准小写 UUID") from None
    return value


def workspace_uuid(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError("工作区编号须为标准小写 UUID") from None
    return value


def main():
    parser = argparse.ArgumentParser(prog="ga")
    parser.add_argument("command", choices=["migrate", "bootstrap", "serve", "worker", "setup-worker", "media-worker", "mqtt-worker", "signal-worker", "source-query-worker", "automation-task-worker", "workflow-task-worker", "state-projection-worker", "http-observation-task-worker"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--job-id", type=job_uuid, metavar="UUID",
                        help="仅供 setup-worker：尝试领取指定现有任务一次，不运行全局调度")
    scope.add_argument("--workspace-id", type=workspace_uuid, metavar="UUID",
                        help="仅供 setup-worker：持续处理指定工作区的全部配置任务")
    parser.add_argument('--subscription-id', type=job_uuid, metavar='UUID', help='仅供automation-task-worker：进一步限定原授权')
    parser.add_argument('--instance-id', type=job_uuid, metavar='UUID', help='工作流或对象投影处理器：明确实例')
    parser.add_argument('--family', choices=['compact','graph','http_mapped_v177'], help='明确工作流或HTTP观测合同族')
    parser.add_argument('--consumer-mode', choices=['jobs','receipts'], help='仅供http-observation-task-worker：任务处理或独立收件')
    parser.add_argument('--task-id', type=job_uuid, metavar='UUID', help='仅供workflow-task-worker：进一步限定原任务')
    parser.add_argument('--entity-id', type=job_uuid, metavar='UUID', help='仅供state-projection-worker：进一步限定对象')
    parser.add_argument('--source-id', type=job_uuid, metavar='UUID', help='仅供state-projection-worker：进一步限定实际来源')
    parser.add_argument('--observation-id', type=job_uuid, metavar='UUID', help='仅供state-projection-worker：进一步限定原登记')
    parser.add_argument('--projection-job-id', type=job_uuid, metavar='UUID', help='仅供state-projection-worker：进一步限定原投影任务')
    args = parser.parse_args()
    if args.command not in {"setup-worker","signal-worker","source-query-worker","automation-task-worker","workflow-task-worker","state-projection-worker","http-observation-task-worker"} and (args.job_id is not None or args.workspace_id is not None):
        parser.error("--job-id 和 --workspace-id 仅适用于 setup-worker")
    if args.command == "setup-worker" and args.job_id is None and args.workspace_id is None:
        parser.error("setup-worker 必须明确提供 --workspace-id UUID 或 --job-id UUID；不再无范围全局启动")
    if args.command == 'signal-worker' and (args.workspace_id is None or args.job_id is not None):
        parser.error('signal-worker 必须明确提供 --workspace-id UUID，且不能领取 Job')
    if args.command == 'source-query-worker' and (args.workspace_id is None or args.job_id is not None):
        parser.error('source-query-worker 必须明确提供 --workspace-id UUID，只处理已证明的本地资料查询族')
    if args.command == 'automation-task-worker' and (args.workspace_id is None or args.job_id is not None):
        parser.error('automation-task-worker 必须明确提供 --workspace-id UUID，只处理已证明的新业务自动任务族')
    if args.subscription_id is not None and args.command != 'automation-task-worker':
        parser.error('--subscription-id 仅适用于 automation-task-worker')
    if args.command == 'workflow-task-worker':
        if args.workspace_id is None or args.instance_id is None or args.family not in {'compact','graph'} or args.job_id is not None:
            parser.error('workflow-task-worker 必须明确 --workspace-id、--instance-id、--family，不能无范围扫描')
    elif args.command == 'http-observation-task-worker':
        if any(value is None for value in (args.workspace_id,args.instance_id,args.task_id,args.consumer_mode)) or args.family != 'http_mapped_v177' or args.job_id is not None:
            parser.error('HTTP观测处理器必须明确工作区、实例、任务、http_mapped_v177合同族及jobs或receipts模式')
    elif args.command == 'state-projection-worker':
        if args.workspace_id is None or args.instance_id is None or args.job_id is not None or args.family is not None or args.task_id is not None:
            parser.error('state-projection-worker 必须明确 --workspace-id 和 --instance-id，不能领取其他类型任务')
    elif any(value is not None for value in (args.instance_id,args.family,args.task_id)):
        parser.error('--instance-id/--family/--task-id 仅适用于 workflow-task-worker')
    if args.consumer_mode is not None and args.command != 'http-observation-task-worker':
        parser.error('--consumer-mode 仅适用于 http-observation-task-worker')
    if args.command != 'state-projection-worker' and any(value is not None for value in (
            args.entity_id,args.source_id,args.observation_id,args.projection_job_id)):
        parser.error('对象、来源、登记和投影原号范围仅适用于 state-projection-worker')
    if args.command == "migrate":
        migrate(); print("数据库初始化完成")
    elif args.command == "bootstrap":
        migrate()
        username = input("管理员用户名：").strip()
        password = getpass.getpass("密码（至少12位）：")
        if getpass.getpass("再次输入密码：") != password:
            raise SystemExit("密码不一致")
        name = input("工作区名称：").strip()
        if not username or not name:
            raise SystemExit("用户名和工作区名称不能为空")
        try:
            encoded = password_hash(password)
            with Session(engine()) as db:
                created = create_administrator(db, username=username, workspace_name=name,
                                               encoded_password=encoded)
                db.commit()
        except ValueError as error:
            raise SystemExit(str(error)) from None
        except SQLAlchemyError:
            # SQLAlchemy exceptions may include bound password_hash parameters.
            # A failed commit response also cannot prove whether it was saved.
            raise SystemExit("管理员创建状态未确认，请核对原账号与实例后恢复。") from None
        print("管理员和工作区已保存")
        print("工作区 UUID：" + created['workspace_id'])
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("generic_agents.api:app", host=args.host, port=args.port)
    elif args.command == "setup-worker":
        if args.job_id is not None:
            from .worker import run_setup_job
            raise SystemExit(run_setup_job(args.job_id))
        from .worker import run_setup
        raise SystemExit(run_setup(args.workspace_id))
    elif args.command == 'media-worker':
        from .worker import run_media
        run_media()
    elif args.command == 'signal-worker':
        from .signal_dual_handoffs import run_worker
        raise SystemExit(run_worker(args.workspace_id))
    elif args.command == 'source-query-worker':
        from .source_query_consumer import run as run_source_queries
        raise SystemExit(run_source_queries(args.workspace_id))
    elif args.command == 'automation-task-worker':
        from .business_automation_consumer import run as run_automations
        raise SystemExit(run_automations(args.workspace_id,subscription_id=args.subscription_id))
    elif args.command == 'workflow-task-worker':
        from .workflow_task_consumer_v265 import run as run_workflows
        raise SystemExit(run_workflows(args.workspace_id,args.instance_id,family=args.family,task_id=args.task_id))
    elif args.command == 'http-observation-task-worker':
        from .http_observation_task_consumer_v270 import run as run_http_observations
        raise SystemExit(run_http_observations(args.workspace_id,args.instance_id,args.task_id,
            family=args.family,mode=args.consumer_mode))
    elif args.command == 'state-projection-worker':
        from .state_projection_consumer_v266 import run as run_projections
        raise SystemExit(run_projections(args.workspace_id,args.instance_id,entity_id=args.entity_id,
            source_id=args.source_id,observation_id=args.observation_id,job_id=args.projection_job_id))
    elif args.command == 'mqtt-worker':
        from .mqtt_supervisor import main as mqtt_main
        mqtt_main()
    else:
        from .worker import run
        run()


if __name__ == "__main__":
    main()
