export type ChannelType = 'fan' | 'light' | 'socket' | 'other';

export interface Channel {
  index: number;
  name: string;
  type: ChannelType;
}

export interface Telemetry {
  rssi?: number | null;
  uptime_s?: number | null;
  free_heap?: number | null;
}

export interface Device {
  id: string;
  hardware_id: string;
  name: string;
  room: string;
  online: boolean;
  last_seen: string | null;
  firmware: string | null;
  local_ip: string | null;
  local_port: number;
  lan_key: string | null;
  channel_count: number;
  channels: Channel[];
  state: boolean[];
  telemetry: Telemetry;
  /** Latched alarm, mirrored from `devices/<id>/info/alarm`. */
  alarm?: boolean;
  created_at: string | null;
}

export interface DevicePatch {
  name?: string;
  room?: string;
  channels?: Channel[];
}

export type PetAction = 'pet' | 'feed' | 'play' | 'sleep' | 'wake';

export type CommandAction =
  | 'set'
  | 'all'
  | 'toggle'
  | PetAction
  | 'alarm'
  | 'alarm_stop'
  | 'reminder';

export interface CommandBody {
  action: CommandAction;
  channel?: number;
  on?: boolean;
}

export interface CommandResult {
  command_id: string;
  acked: boolean;
  online: boolean;
  state: boolean[];
  error?: string | null;
}

export type ScheduleActionType = 'set_all' | 'set_channel' | 'alarm' | 'reminder';

export interface ScheduleAction {
  type: ScheduleActionType;
  on?: boolean | null;
  channel?: number | null;
}

export interface Schedule {
  id: string;
  device_id: string;
  name: string;
  time: string;
  days: number[];
  timezone: string;
  action: ScheduleAction;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string | null;
}

export interface ScheduleInput {
  device_id: string;
  name: string;
  time: string;
  days: number[];
  timezone: string;
  action: ScheduleAction;
  enabled?: boolean;
}

export interface AssistantLink {
  id: string;
  /** OAuth client the token was issued to: "google" or "smartthings". */
  provider: string;
  client_name: string;
  created_at: string | null;
  last_used_at: string | null;
}
