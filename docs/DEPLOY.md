# Deployment

## Deploy targets

| Target | Where |
| --- | --- |
| AWS ECS Fargate + RDS Postgres + ElastiCache Redis | `deploy/ecs/` |
| Docker Compose (self-contained) | `docker-compose.prod.yml` |

Secrets are passed via environment variables or AWS SSM and are **never
committed** to the repository.

## Step-by-step: ECS Fargate

### 1. One-time infrastructure (CloudFormation)

```bash
aws cloudformation deploy \
  --stack-name llm-eval-harness \
  --template-file deploy/ecs/infrastructure.yml \
  --parameter-overrides \
    VpcId=vpc-xxxx \
    PrivateSubnetA=subnet-xxxx \
    PrivateSubnetB=subnet-yyyy \
    DBPassword='<a strong password>' \
  --capabilities CAPABILITY_IAM
```

This creates:

- an ECR repository (`llm-eval-harness`)
- an RDS Postgres 16 instance (`db.t4g.micro`, encrypted)
- an ElastiCache Redis 7 cluster (`cache.t4g.micro`)
- SSM parameter skeletons under `/llm-eval-harness/*`

### 2. Write real secrets to SSM

The template creates placeholders. Overwrite them:

```bash
aws ssm put-parameter --name /llm-eval-harness/database_url \
  --value "postgresql+asyncpg://eval:<password>@<rds-host>:5432/eval" \
  --type String --overwrite

aws ssm put-parameter --name /llm-eval-harness/redis_url \
  --value "redis://<elasticache-host>:6379/0" --type String --overwrite

aws ssm put-parameter --name /llm-eval-harness/openai_api_key \
  --value "sk-..." --type String --overwrite

aws ssm put-parameter --name /llm-eval-harness/anthropic_api_key \
  --value "sk-ant-..." --type String --overwrite
```

### 3. Push the image to ECR

Pushing happens automatically on every push to `main`
(`.github/workflows/deploy.yml`), which requires:

- GitHub Actions secret `AWS_ACCOUNT_ID`
- GitHub Actions secret `AWS_REGION`
- an IAM role `github-actions-ecr` trustable by the repo, with
  `ecr:GetAuthorizationToken` / `ecr:BatchCheckLayerAvailability` /
  `ecr:GetDownloadUrlForLayer` / `ecr:BatchGetImage` / `ecr:PutImage`

Or manually:

```bash
aws ecr get-login-password --region <region> | docker login --username AWS \
  --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build -t llm-eval-harness .
docker tag llm-eval-harness:latest <account>.dkr.ecr.<region>.amazonaws.com/llm-eval-harness:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/llm-eval-harness:latest
```

### 4. Migrations before rollout

Run Alembic against the RDS instance **before** pointing the new task
revision at it. Either run a one-off Fargate task or from a machine with
network access to RDS:

```bash
DATABASE_URL="postgresql+asyncpg://eval:<password>@<rds-host>:5432/eval" \
  alembic upgrade head
```

Strategy: migrations are always backward-compatible (additive columns/table),
so upgrade first, then deploy the new api/worker revisions. A one-off ECS
task (`--command-override alembic upgrade head`) works too:

```bash
aws ecs run-task --cluster <cluster> --task-definition llm-eval-harness-api \
  --network-configuration '{"awsvpcConfiguration":{"subnets":["subnet-xxx"],"securityGroups":["sg-yyy"]}}' \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}'
```

### 5. Register task definitions and run services

Substitute `${AWS_ACCOUNT_ID}` / `${AWS_REGION}` in
`deploy/ecs/task-definition-api.json` and
`deploy/ecs/task-definition-worker.json` (e.g. with `envsubst`), then:

```bash
aws ecs register-task-definition --cli-input-json file://task-definition-api.json
aws ecs register-task-definition --cli-input-json file://task-definition-worker.json
```

Run the api behind an Application Load Balancer (health check path
`/healthz`, port 8000) and the worker as a service without a load balancer.

### 6. Create the first API key

After the api service is healthy:

```bash
docker run --rm -e DATABASE_URL=... llm-eval-harness \
  python scripts/create_api_key.py bootstrap-admin admin
```

The raw key is printed once; store it in your secret manager.

## Step-by-step: docker-compose.prod.yml

```bash
cp .env.example .env          # then fill in real values
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml run --rm api \
  python scripts/create_api_key.py bootstrap-admin admin
```

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | yes | dev default | `postgresql+asyncpg://...` |
| `REDIS_URL` | yes | dev default | broker + artifacts |
| `ENVIRONMENT` | no | `development` | `production` enables stricter settings |
| `EVAL_PROVIDER` | no | `fake` | `fake` / `openai` / `anthropic` |
| `OPENAI_API_KEY` | only for openai | - | provider key |
| `ANTHROPIC_API_KEY` | only for anthropic | - | provider key |
| `JUDGE_MODEL_ID` | no | `gpt-4o-mini` | model used by the LLM judge |
| `MAX_SPEND_PER_RUN_USD` | no | `25.00` | per-eval-run abort threshold |
| `DISAGREEMENT_THRESHOLD` | no | `0.5` | review trigger |
| `LOW_CONFIDENCE_MIN/MAX` | no | `0.4` / `0.6` | review trigger band |
| `RETRY_MAX_ATTEMPTS` | no | `5` | provider retry budget |
| `PROMETHEUS_MULTIPROC_DIR` | no | - | set to enable multiproc metrics |
| `WORKER_CONCURRENCY` | no | `4` | Celery worker concurrency |
