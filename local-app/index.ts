/// <reference types="@google/local-home-sdk" />

import App = smarthome.App;
import Constants = smarthome.Constants;
import DataFlow = smarthome.DataFlow;
import Execute = smarthome.Execute;
import IntentFlow = smarthome.IntentFlow;
import Intents = smarthome.Intents;

const APP_VERSION = '1.0.0';
// A FanCtl-8 is one local box with eight relay channels. The hub is identified
// as a proxy; each channel is a reachable end device.
const CHANNELS = 8;
const HTTP_PORT = 80;

interface ChannelData {
  /** Controller id: the same value in the mDNS TXT record and the cloud docs. */
  id: string;
  channel: number;
  /** Assistant key minted by the backend, never the owner's device key. */
  key: string;
}

interface BoardState {
  state?: boolean[];
}

const localHomeApp = new App(APP_VERSION);

function channelData(device: { customData?: unknown }): ChannelData | null {
  const data = device.customData as Partial<ChannelData> | undefined;
  if (!data || typeof data.id !== 'string' || typeof data.key !== 'string') return null;
  if (typeof data.channel !== 'number' || data.channel < 0 || data.channel >= CHANNELS) {
    return null;
  }
  return data as ChannelData;
}

async function boardCall<T>(
  requestId: string,
  data: ChannelData,
  method: smarthome.Constants.HttpOperation,
  path: string,
  body?: unknown,
): Promise<T> {
  const command = new DataFlow.HttpRequestData();
  command.requestId = requestId;
  // The identified proxy is the box; the platform resolves it to the address
  // it saw in the mDNS scan.
  command.deviceId = data.id;
  command.method = method;
  command.port = HTTP_PORT;
  command.path = path;
  command.additionalHeaders = { 'X-Local-Key': data.key };
  if (body !== undefined) {
    command.dataType = 'application/json';
    command.data = JSON.stringify(body);
  }
  const result = (await localHomeApp.getDeviceManager().send(command)) as DataFlow.HttpResponseData;
  return result.httpResponse.body as T;
}

function onOff(execution: IntentFlow.ExecuteRequestExecution[]): boolean | null {
  let on: boolean | null = null;
  for (const step of execution) {
    if (step.command !== 'action.devices.commands.OnOff') return null;
    const params = step.params as { on?: unknown } | undefined;
    if (typeof params?.on !== 'boolean') return null;
    on = params.on;
  }
  return on;
}

localHomeApp.onIdentify((request: IntentFlow.IdentifyRequest): IntentFlow.IdentifyResponse => {
  const scan = request.inputs[0].payload.device.mdnsScanData;
  const id = scan?.txt?.id;
  if (!id) {
    throw new IntentFlow.HandlerError(
      request.requestId,
      IntentFlow.ErrorCode.DEVICE_NOT_IDENTIFIED,
      'FanCtl TXT record has no id',
    );
  }
  return {
    intent: Intents.IDENTIFY,
    requestId: request.requestId,
    payload: { device: { id, isProxy: true, isLocalOnly: true } },
  };
});

localHomeApp.onReachableDevices(
  (request: IntentFlow.ReachableDevicesRequest): IntentFlow.ReachableDevicesResponse => {
    const hubId = request.inputs[0].payload.device.id;
    if (!hubId) {
      throw new IntentFlow.HandlerError(
        request.requestId,
        IntentFlow.ErrorCode.DEVICE_NOT_IDENTIFIED,
        'proxy id missing',
      );
    }
    const devices: IntentFlow.DeviceWithVerificationId[] = [];
    for (let channel = 0; channel < CHANNELS; channel++) {
      // Must match the otherDeviceIds in the cloud SYNC response.
      devices.push({ verificationId: `${hubId}.ch${channel}` });
    }
    return {
      intent: Intents.REACHABLE_DEVICES,
      requestId: request.requestId,
      payload: { devices },
    };
  },
);

localHomeApp.onQuery(async (request: IntentFlow.QueryRequest): Promise<IntentFlow.QueryResponse> => {
  const devices: Record<string, unknown> = {};
  const boards = new Map<string, ChannelData>();

  for (const device of request.inputs[0].payload.devices) {
    const data = channelData(device);
    if (!data) {
      devices[device.id] = { status: 'ERROR', errorCode: 'deviceNotFound' };
      continue;
    }
    boards.set(data.id, data);
  }

  // One GET per controller, then slice out the channels Google asked for.
  const states = new Map<string, boolean[] | null>();
  for (const [boardId, data] of boards) {
    try {
      const body = await boardCall<BoardState>(
        request.requestId,
        data,
        Constants.HttpOperation.GET,
        '/api/state',
      );
      states.set(boardId, Array.isArray(body.state) && body.state.length === CHANNELS ? body.state : null);
    } catch {
      states.set(boardId, null);
    }
  }

  for (const device of request.inputs[0].payload.devices) {
    const data = channelData(device);
    if (!data) continue;
    const state = states.get(data.id);
    if (!state) {
      devices[device.id] = { status: 'ERROR', errorCode: 'deviceOffline' };
    } else {
      devices[device.id] = { status: 'SUCCESS', on: Boolean(state[data.channel]), online: true };
    }
  }

  return { requestId: request.requestId, payload: { devices } };
});

localHomeApp.onExecute(async (request: IntentFlow.ExecuteRequest): Promise<IntentFlow.ExecuteResponse> => {
  const response = new Execute.Response.Builder().setRequestId(request.requestId);
  const pending: Array<Promise<void>> = [];

  for (const command of request.inputs[0].payload.commands) {
    const on = onOff(command.execution);
    for (const device of command.devices) {
      const data = channelData(device);
      if (!data) {
        response.setErrorState(device.id, 'deviceNotFound');
        continue;
      }
      if (on === null) {
        response.setErrorState(device.id, 'functionNotSupported');
        continue;
      }
      pending.push(
        boardCall(request.requestId, data, Constants.HttpOperation.POST, '/api/command', {
          action: 'set',
          ch: data.channel,
          on,
        })
          .then(() => {
            response.setSuccessState(device.id, { on, online: true });
          })
          .catch(() => {
            response.setErrorState(device.id, 'deviceOffline');
          }),
      );
    }
  }

  await Promise.all(pending);
  return response.build();
});

localHomeApp
  .listen()
  .then(() => console.log('FanCtl local fulfillment ready'))
  .catch((error: Error) => console.error('FanCtl local fulfillment failed', error));
