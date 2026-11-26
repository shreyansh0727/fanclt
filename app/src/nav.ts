export type RootStackParamList = {
  Auth: undefined;
  Devices: undefined;
  Device: { deviceId: string; name: string };
  AddDevice: undefined;
  Schedules: { deviceId: string; deviceName: string };
  Assistants: undefined;
};
