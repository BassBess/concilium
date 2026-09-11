export interface ProviderSettingsField {
  set?: boolean;
  masked?: string;
  source?: string;
  [k: string]: any;
}

export interface ProviderInstance {
  id: string;
  type: string;
  display_name: string;
  kind: "local" | "remote";
  label: string;
  enabled: boolean;
  builtin: boolean;
  configured: boolean;
  settings: Record<string, ProviderSettingsField | any>;
  docs_url?: string;
  signup_url?: string;
  secret_fields: string[];
  setting_fields: string[];
  supports_multiple_instances: boolean;
}

export interface ProviderType {
  type: string;
  display_name: string;
  kind: "local" | "remote";
  docs_url: string;
  signup_url: string;
  secret_fields: string[];
  setting_fields: string[];
  default_base_url: string;
  supports_multiple_instances: boolean;
}

export interface ModelInfo {
  id: string;
  provider: string;
  provider_type?: string;
  provider_name?: string;
  name: string;
  context_window: number;
  max_output: number;
  capabilities: string[];
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  tier: string;
  free_tier: boolean;
  family: string;
  description?: string;
  instance_id?: string | null;
  configured?: boolean;
  available?: boolean;
  uid?: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  run_id?: string;
  role: "user" | "assistant" | "system" | "agent";
  content: string;
  agent?: string | null;
  provider?: string | null;
  model?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  latency_ms: number;
  status: string;
  kind: string;
  meta: any;
  created_at: string;
}

export interface Conversation {
  id: string;
  project_id: string;
  title: string;
  mode: string;
  config: any;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  config: any;
  created_at: string;
}

export interface Workflow {
  id?: string;
  name: string;
  description?: string;
  project_id?: string | null;
  definition: WorkflowDefinition | CouncilDefinition;
  builtin?: boolean;
}

export interface WorkflowNode {
  id: string;
  role: string;
  count?: number;
  depends_on?: string[];
  terminal?: boolean;
  tools?: boolean;
  ref?: ModelRef;
  instruction?: string;
}

export interface WorkflowDefinition {
  nodes: WorkflowNode[];
}

export interface Participant {
  label: string;
  role: string;
  ref: ModelRef;
  allow_tools?: boolean;
  system?: string;
}

export interface CouncilDefinition {
  mode?: string;
  participants: Participant[];
  parallel: boolean;
  critique_rounds: number;
  revision: boolean;
  research?: boolean;
  failover?: boolean;
  temperature?: number;
  synthesizer: { role?: string; ref: ModelRef };
}

export interface ModelRef {
  provider_id?: string;
  provider_type?: string;
  model: string;
}

export interface TraceEvent {
  seq?: number;
  ts?: number;
  type: string;
  [k: string]: any;
}

export interface ToolDescriptor {
  key: string;
  display_name: string;
  description: string;
  category: string;
  enabled: boolean;
  available: boolean;
  unavailable_reason: string;
  parameters: any;
  permissions: any;
  settings: Record<string, any>;
}

export interface PromptTemplate {
  id: string;
  name: string;
  kind: string;
  content: string;
  variables: string[];
}
