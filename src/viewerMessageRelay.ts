import { sendMessage, type MessageSender, type ViewerMessage } from '../shared/messages.js';

export class ViewerMessageRelay implements MessageSender {
  constructor(
    private readonly getLocalSender: () => MessageSender | undefined,
    private readonly publishRemote: (message: ViewerMessage) => void,
  ) {}

  postMessage(message: ViewerMessage): void {
    sendMessage(this.getLocalSender(), message);
    this.publishRemote(message);
  }
}