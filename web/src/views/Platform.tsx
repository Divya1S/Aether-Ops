import { Badge, Card, ErrorNote, Icon, Loading, PageHead, RiskBadge, useApp, useAsync } from "../components/ui";

const ROLES = ["viewer", "operator", "approver", "ingest", "admin"];
const GRANT_HELP: Record<string, string> = {
  read: "See incidents, alerts, evidence and audit ledgers",
  create: "Start investigations and score changes",
  approve: "Decide approval gates",
  ingest: "Post alerts (webhook credential)",
  feedback: "Mark a diagnosis correct or incorrect",
};

export function Platform() {
  const { client, me } = useApp();
  const { data, error, reload } = useAsync(
    async () => {
      const [catalog, connectors, openapi] = await Promise.all([client.catalog(), client.connectors(), client.openapi()]);
      return { catalog, connectors, openapi };
    },
    [client],
  );
  if (error) return <ErrorNote error={error} retry={reload} />;
  if (!data) return <Loading />;
  const { catalog, connectors, openapi } = data;
  const routes = Object.entries(openapi.paths).flatMap(([path, methods]) =>
    Object.entries(methods).map(([method, operation]) => ({ path, method: method.toUpperCase(), operation })),
  );

  return (
    <>
      <PageHead title="Governance">
        The governed surface, as data: what the platform is able to do, who may ask it to, and what stands between a
        model's proposal and a production write.
      </PageHead>

      <div className="stack">
        <div className="grid half">
          <Card
            title="Step Catalog — the only actions that can execute"
            flush
            foot="A plan step outside this catalog fails compilation. Write-risk tools are callable only by the control plane's executor principal — never by an agent."
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Risk</th>
                    <th>Requires</th>
                    <th>Undo</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.step_catalog.map((step) => (
                    <tr key={step.action}>
                      <td>
                        <code>{step.action}</code>
                        <div className="dim small">
                          {step.system}.{step.tool}
                        </div>
                      </td>
                      <td>
                        <RiskBadge risk={step.risk} />
                      </td>
                      <td className="small mono">{step.required_args.join(", ")}</td>
                      <td className="small muted">{step.compensable ? "saga" : "none"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="stack">
            <Card title="Investigation tools — what the model may choose" flush>
              <table>
                <tbody>
                  {catalog.investigation_tools.map((tool) => (
                    <tr key={tool.name}>
                      <td>
                        <code>{tool.name}</code>
                        <div className="muted small">{tool.description}</div>
                      </td>
                      <td className="num">
                        <RiskBadge risk={tool.risk} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card title="Connectors">
              <ul className="list-plain">
                {Object.entries(connectors).map(([slot, kind]) => (
                  <li key={slot} className="row spread">
                    <code>{slot}</code>
                    <Badge tone={kind.startsWith("real") ? "good" : "neutral"}>
                      {kind.startsWith("real") ? kind.replace("real:", "live · ") : "snapshot fake"}
                    </Badge>
                  </li>
                ))}
              </ul>
              <p className="dim small" style={{ marginTop: "0.6rem" }}>
                Fakes serve frozen evidence so demos, evals and CI stay network-free. Set{" "}
                <code>AETHEROPS_GITHUB_REPO</code> or <code>AETHEROPS_PROMETHEUS_URL</code> to swap in the real adapters.
              </p>
            </Card>
          </div>
        </div>

        <Card
          title="Roles and grants"
          flush
          foot={
            <>
              You are signed in as <b>{me?.role ?? "unknown"}</b>. Access control runs before any handler: an unknown
              token is 401, a known one without the grant is 403.
            </>
          }
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Grant</th>
                  {ROLES.map((role) => (
                    <th key={role} style={{ textAlign: "center" }}>
                      {role}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(catalog.role_grants).map(([grant, roles]) => (
                  <tr key={grant}>
                    <td>
                      <code>{grant}</code>
                      <div className="muted small">{GRANT_HELP[grant]}</div>
                    </td>
                    {ROLES.map((role) => (
                      <td key={role} style={{ textAlign: "center" }}>
                        {roles.includes(role) ? (
                          <span style={{ color: "var(--good-ink)" }}>
                            <Icon name="check" />
                            <span className="sr-only">allowed</span>
                          </span>
                        ) : (
                          <span className="dim">
                            —<span className="sr-only">not allowed</span>
                          </span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="grid two">
          <Card
            title={`API · ${routes.length} endpoints`}
            flush
            foot={
              <>
                Generated from the server's route table — <code>GET /v1/openapi.json</code>.
              </>
            }
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Endpoint</th>
                    <th>Does</th>
                    <th>Grant</th>
                  </tr>
                </thead>
                <tbody>
                  {routes.map(({ path, method, operation }) => (
                    <tr key={`${method} ${path}`}>
                      <td className="nowrap">
                        <Badge mono tone={method === "GET" ? "neutral" : "accent"}>
                          {method}
                        </Badge>{" "}
                        <code>{path}</code>
                      </td>
                      <td className="small muted">{operation.summary}</td>
                      <td className="small mono">
                        {operation["x-required-grant"] === "*" ? "any role" : (operation["x-required-grant"] ?? "public")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="Pinned prompt versions" foot="Each template's sha256 is locked; editing one without bumping its version fails the build.">
            <div className="row tight">
              {catalog.prompts.map((prompt) => (
                <span className="chip" key={prompt}>
                  {prompt}
                </span>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
