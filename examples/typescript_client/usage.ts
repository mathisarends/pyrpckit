import { TaskClient, TaskStatus } from "./generated";
import type { RpcTransport } from "./transport";

export async function useTaskApi(transport: RpcTransport): Promise<void> {
  const client = new TaskClient(transport);

  const created = await client.tasks.create({ title: "Read generated client" });
  await client.tasks.status.set({
    taskId: created.id,
    status: TaskStatus.DONE,
  });

  const { tasks } = await client.tasks.list();
  console.log(tasks);

  for await (const event of client.events()) {
    console.log(event.type, event.task);
  }
}
