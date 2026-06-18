# %%
# @title Imports
import asyncio
import os

import httpx
import pandas as pd
import yaml
from IPython.display import display
from kubernetes import client, config

try:
    from google.colab import userdata
except ImportError:
    userdata = None


# %%
# @title Config
TOP_WASTE_SPREADSHEET_ID = "13JavgeYNFHSKqmtIyBDZvOBpWCgiwOevW9MFNp_OzsE"
PROJECT_USERS_SPREADSHEET_ID = "1tTPSyqcTwEe_xddzPoPDxjAQWLNOVzVFaAJhQvnicsQ"
TOP_N_NAMESPACES = 15  # how many namespaces to load from latest snapshot
RANCHER_URL = "https://rancher.smile.fr"
WORKSHEET_DATE_FORMAT = "%Y-%m-%d"


# %%
# @title Load Clients
def load_secret_or_env(name, explicit_value=""):
    if userdata is not None:
        try:
            value = userdata.get(name)
            if value:
                return str(value).strip()
        except Exception:
            pass

    env_value = os.environ.get(name, "").strip()
    if env_value:
        return env_value

    return explicit_value.strip()


def load_kubeconfig_text():
    raw_config = load_secret_or_env("KUBECONFIG")
    if not raw_config:
        print(
            "Warning: set Colab Secret 'KUBECONFIG' or export "
            "KUBECONFIG=/path/to/config before running."
        )
        return None

    kubeconfig_path = os.path.expanduser(raw_config)
    if os.path.exists(kubeconfig_path):
        with open(kubeconfig_path, encoding="utf-8") as handle:
            return handle.read()

    return raw_config


def get_kubernetes_client():
    raw_config = load_kubeconfig_text()
    if not raw_config:
        return None

    try:
        config_dict = yaml.safe_load(raw_config)
        loader = config.kube_config.KubeConfigLoader(config_dict)
        k8s_config = client.Configuration()
        loader.load_and_set(k8s_config)
        api_client = client.ApiClient(configuration=k8s_config)
        return client.CoreV1Api(api_client)
    except Exception as exc:
        print(f"Kubernetes initialization failed: {exc}")
        return None


v1_client = get_kubernetes_client()
rancher_url = load_secret_or_env("RANCHER_URL", RANCHER_URL)
rancher_token = load_secret_or_env("RANCHER_TOKEN")


# %%
# @title Project Users Export
USER_COLUMNS = [
    "namespace",
    "project_id",
    "project_name",
    "subject_type",
    "subject_id",
    "subject_name",
    "role_template_id",
    "role_template_name",
    "user_username",
    "user_display_name",
    "user_name",
    "user_email",
    "user_enabled",
]


def get_google_sheet(spreadsheet_id):
    from google.auth import default

    try:
        from google.colab import auth

        auth.authenticate_user()
    except ImportError:
        pass

    import gspread

    creds, _ = default()
    gc = gspread.authorize(creds)
    return gc.open_by_key(spreadsheet_id)


def build_snapshot_worksheet_name(date_format=WORKSHEET_DATE_FORMAT):
    return pd.Timestamp.now().strftime(date_format)


def write_df_to_worksheet(df, spreadsheet_id, worksheet_name):
    if df.empty:
        print(f"Skipped {worksheet_name}: DataFrame is empty.")
        return

    from gspread_dataframe import set_with_dataframe

    spreadsheet = get_google_sheet(spreadsheet_id)
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
        worksheet.clear()
    except Exception:
        worksheet = spreadsheet.add_worksheet(
            title=worksheet_name,
            rows=max(len(df) + 10, 100),
            cols=max(len(df.columns) + 10, 20),
        )

    set_with_dataframe(worksheet, df)
    print(f"Wrote {len(df)} rows to {spreadsheet_id}/{worksheet_name}")


def load_target_namespaces(spreadsheet_id, top_n):
    spreadsheet = get_google_sheet(spreadsheet_id)
    dated_worksheets = []
    for worksheet in spreadsheet.worksheets():
        try:
            worksheet_date = pd.to_datetime(worksheet.title, format="%Y-%m-%d")
        except ValueError:
            continue
        dated_worksheets.append((worksheet_date, worksheet))

    if not dated_worksheets:
        raise ValueError("No dated worksheets found in top waste spreadsheet")

    _, latest_worksheet = max(dated_worksheets, key=lambda item: item[0])
    top_waste_df = pd.DataFrame(latest_worksheet.get_all_records())
    if top_waste_df.empty:
        raise ValueError(f"{latest_worksheet.title} worksheet is empty")
    if "namespace" not in top_waste_df.columns:
        raise ValueError(f"{latest_worksheet.title} does not contain 'namespace'")

    if "cpu_request_waste_m" in top_waste_df.columns:
        top_waste_df = top_waste_df.sort_values(
            "cpu_request_waste_m",
            ascending=False,
        )

    return set(top_waste_df["namespace"].head(top_n).astype(str))


def extract_rancher_project(namespace_item):
    metadata = namespace_item.metadata
    annotations = metadata.annotations or {}
    labels = metadata.labels or {}
    project_id = (
        annotations.get("field.cattle.io/projectId")
        or labels.get("field.cattle.io/projectId")
        or ""
    )
    project_name = annotations.get("field.cattle.io/projectName") or project_id
    return {
        "project_id": project_id,
        "project_name": project_name,
    }


def get_namespace_project_mapping(v1, target_namespaces):
    if not v1:
        return []

    namespace_infos = []
    for namespace_item in v1.list_namespace().items:
        namespace = namespace_item.metadata.name
        if namespace not in target_namespaces:
            continue

        project_meta = extract_rancher_project(namespace_item)
        namespace_infos.append(
            {
                "namespace": namespace,
                "project_id": project_meta["project_id"],
                "project_name": project_meta["project_name"],
            }
        )

    if not namespace_infos:
        raise ValueError("None of the requested namespaces were found")
    return namespace_infos


def build_project_mapping(namespace_infos):
    projects = {}
    for info in namespace_infos:
        project_id = info["project_id"]
        project_key = project_id or f"standalone-{info['namespace']}"
        if project_key not in projects:
            projects[project_key] = {
                "project_id": project_id,
                "project_name": info["project_name"],
                "namespaces": [info["namespace"]],
                "bindings": [],
            }
            continue
        projects[project_key]["namespaces"].append(info["namespace"])
    return projects


async def rancher_get(client, path, token, params=None):
    response = await client.get(
        path,
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    response.raise_for_status()
    return response.json()


async def fetch_project_bindings(client, token, projects):
    tasks = {}
    for project_key, project_data in projects.items():
        project_id = project_data["project_id"]
        if not project_id:
            continue
        tasks[project_key] = asyncio.create_task(
            rancher_get(
                client,
                "/v3/projectroletemplatebindings",
                token,
                params={"projectId": project_id},
            )
        )

    for project_key, task in tasks.items():
        data = await task
        projects[project_key]["bindings"] = data.get("data", [])


async def fetch_user_map(client, token, projects):
    user_ids = {
        binding.get("userId", "")
        for project_data in projects.values()
        for binding in project_data.get("bindings", [])
        if binding.get("userId")
    }
    tasks = {
        user_id: asyncio.create_task(rancher_get(client, f"/v3/users/{user_id}", token))
        for user_id in user_ids
    }

    user_map = {}
    for user_id, task in tasks.items():
        try:
            user_map[user_id] = await task
        except httpx.HTTPError:
            user_map[user_id] = {}
    return user_map


def build_project_users_df(projects, user_map):
    rows = []
    for project_data in projects.values():
        project_id = project_data["project_id"]
        project_name = project_data["project_name"] or project_id
        namespaces = project_data["namespaces"]
        bindings = project_data["bindings"]

        if not bindings:
            for namespace in namespaces:
                rows.append(
                    {
                        "namespace": namespace,
                        "project_id": project_id,
                        "project_name": project_name,
                        "subject_type": "(none)",
                        "subject_id": "",
                        "subject_name": "",
                        "role_template_id": "",
                        "role_template_name": "",
                        "user_username": "",
                        "user_display_name": "",
                        "user_name": "",
                        "user_email": "",
                        "user_enabled": "",
                    }
                )
            continue

        for namespace in namespaces:
            for binding in bindings:
                user_id = binding.get("userId", "")
                subject_type = "user" if user_id else "group"
                subject_id = user_id or binding.get("groupPrincipalId", "")
                user_details = user_map.get(user_id, {}) if user_id else {}
                rows.append(
                    {
                        "namespace": namespace,
                        "project_id": project_id,
                        "project_name": project_name,
                        "subject_type": subject_type,
                        "subject_id": subject_id,
                        "subject_name": binding.get("userName")
                        or binding.get("groupName", ""),
                        "role_template_id": binding.get("roleTemplateId", ""),
                        "role_template_name": binding.get("roleTemplateName", ""),
                        "user_username": user_details.get("username", ""),
                        "user_display_name": user_details.get("displayName", ""),
                        "user_name": user_details.get("name", ""),
                        "user_email": user_details.get("email", ""),
                        "user_enabled": user_details.get("enabled", ""),
                    }
                )

    return pd.DataFrame(rows, columns=USER_COLUMNS)


async def load_project_users_df(v1, rancher_url, rancher_token):
    if not v1:
        return pd.DataFrame(columns=USER_COLUMNS)
    if not rancher_url:
        raise ValueError("RANCHER_URL is not set")
    if not rancher_token:
        raise ValueError("RANCHER_TOKEN is not set")

    target_namespaces = load_target_namespaces(
        TOP_WASTE_SPREADSHEET_ID,
        TOP_N_NAMESPACES,
    )
    namespace_infos = get_namespace_project_mapping(v1, target_namespaces)
    projects = build_project_mapping(namespace_infos)

    async with httpx.AsyncClient(
        base_url=rancher_url.rstrip("/"),
        timeout=30.0,
    ) as http_client:
        await fetch_project_bindings(http_client, rancher_token, projects)
        user_map = await fetch_user_map(http_client, rancher_token, projects)

    return build_project_users_df(projects, user_map)


def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import nest_asyncio

    nest_asyncio.apply()
    return loop.run_until_complete(coro)


project_users_df = run_async(
    load_project_users_df(
        v1_client,
        rancher_url,
        rancher_token,
    )
)
display(project_users_df)


# %%
# @title Save Project Users
project_users_worksheet = build_snapshot_worksheet_name()
write_df_to_worksheet(
    project_users_df,
    PROJECT_USERS_SPREADSHEET_ID,
    project_users_worksheet,
)
