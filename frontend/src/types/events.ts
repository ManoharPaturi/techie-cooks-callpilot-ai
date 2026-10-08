export type Source = 'HOST' | 'REMOTE';

export interface Utterance {
  type: 'transcript.final';
  id: string;
  session_id: string;
  source: Source;
  origin: 'replay' | 'live';
  text: string;
  start_ms: number;
  end_ms: number;
}

export interface EvidenceLine { id: string; source: Source; text: string; start_ms: number }
export interface NoteParagraph { id: string; filename: string; content: string }

export interface SafetyCandidate { type: 'safety.candidate'; session_id?: string; trigger_id: string; category: string; status: 'checking_context' }

export interface SafetyAssessment {
  type: 'safety.assessment';
  trigger_id: string;
  transcript_ids: string[];
  intent: 'request' | 'mention' | 'warning' | 'uncertain';
  category: string;
  severity: 'none' | 'caution' | 'high' | 'uncertain';
  explanation: string;
  suggested_action: string;
  alert: 'danger' | 'caution' | 'cleared' | 'info' | 'uncertain';
  evidence: EvidenceLine[];
  model: string;
  fallback_reason: string | null;
  latency_ms: number | null;
}

export interface CoachSuggestion {
  type: 'coach.suggestion';
  id: string;
  question: string;
  transcript_ids: string[];
  note_paragraph_ids: string[];
  text: string;
  found_in_notes: boolean;
  basis: 'notes' | 'conversation' | 'general' | 'not_found';
  evidence: EvidenceLine[];
  paragraphs: NoteParagraph[];
  audience: 'HOST_ONLY';
  proactive?: boolean;
  trigger_id?: string | null;
  model: string;
  fallback_reason: string | null;
  latency_ms: number | null;
}

export interface TaskProposal {
  id: string;
  description: string;
  due_text: string;
  transcript_ids: string[];
  evidence: EvidenceLine[];
  status: 'proposed' | 'approved' | 'dismissed';
}

export interface CallSummary {
  type: 'call.summary';
  session_id: string;
  summary: string;
  tasks: TaskProposal[];
  model: string;
  fallback_reason: string | null;
}

export type AsrState = 'idle' | 'speaking' | 'processing' | 'dropped' | 'error';

export type ServerEvent =
  | Utterance
  | SafetyCandidate
  | SafetyAssessment
  | CoachSuggestion
  | CallSummary
  | { type: 'hello'; asr_model: string; llm_model: string }
  | { type: 'asr.status'; source: Source; state: AsrState; detail?: string }
  | { type: 'task.updated'; task: TaskProposal }
  | { type: 'room.status'; state: string; reason?: string }
  | { type: 'session.started' | 'session.stopped'; session_id: string };
