import hashlib
import hmac
import logging
from fbmessenger import MessengerClient
from fbmessenger.attachments import Image
from fbmessenger.elements import Text as FBText
from fbmessenger.quick_replies import QuickReplies, QuickReply
from fbmessenger.sender_actions import SenderAction

import rasa.shared.utils.io
from sanic import Blueprint, response
from sanic.request import Request
from typing import Text, List, Dict, Any, Callable, Awaitable, Iterable, Optional

from rasa.core.channels.channel import UserMessage, OutputChannel, InputChannel
from sanic.response import HTTPResponse

logger = logging.getLogger(__name__)


#Create by Rafael Inácio

class Messenger:
    @classmethod
    def name(cls) -> Text:
        return "instagram"

    def __init__(
        self,
        page_access_token: Text,
        on_new_message: Callable[[UserMessage], Awaitable[Any]],
    ) -> None:

        self.on_new_message = on_new_message
        self.client = MessengerClient(page_access_token)
        self.last_message: Dict[Text, Any] = {}

    def get_user_id(self) -> Text:
        return self.last_message.get("sender", {}).get("id", "")

    @staticmethod
    def _is_audio_message(message: Dict[Text, Any]) -> bool:
        return (
            "message" in message
            and "attachments" in message["message"]
            and message["message"]["attachments"][0]["type"] == "audio"
        )

    @staticmethod
    def _is_image_message(message: Dict[Text, Any]) -> bool:
        return (
            "message" in message
            and "attachments" in message["message"]
            and message["message"]["attachments"][0]["type"] == "image"
        )

    @staticmethod
    def _is_video_message(message: Dict[Text, Any]) -> bool:
        return (
            "message" in message
            and "attachments" in message["message"]
            and message["message"]["attachments"][0]["type"] == "video"
        )

    @staticmethod
    def _is_file_message(message: Dict[Text, Any]) -> bool:
        return (
            "message" in message
            and "attachments" in message["message"]
            and message["message"]["attachments"][0]["type"] == "file"
        )

    @staticmethod
    def _is_user_message(message: Dict[Text, Any]) -> bool:
        return (
            "message" in message
            and "text" in message["message"]
            and not message["message"].get("is_echo")
        )

    @staticmethod
    def _is_quick_reply_message(message: Dict[Text, Any]) -> bool:
        return (
            message.get("message") is not None
            and message["message"].get("quick_reply") is not None
            and message["message"]["quick_reply"].get("payload")
        )

    async def handle(self, payload: Dict, metadata: Optional[Dict[Text, Any]]) -> None:
        for entry in payload["entry"]:
            for message in entry["messaging"]:
                self.last_message = message
                if message.get("message"):
                    return await self.message(message, metadata)
                elif message.get("postback"):
                    return await self.postback(message, metadata)

    async def message(
        self, message: Dict[Text, Any], metadata: Optional[Dict[Text, Any]]
    ) -> None:

        if self._is_quick_reply_message(message):
            text = message["message"]["quick_reply"]["payload"]
        elif self._is_user_message(message):
            text = message["message"]["text"]
        elif self._is_audio_message(message):
            attachment = message["message"]["attachments"][0]
            text = attachment["payload"]["url"]
        elif self._is_image_message(message):
            attachment = message["message"]["attachments"][0]
            text = attachment["payload"]["url"]
        elif self._is_video_message(message):
            attachment = message["message"]["attachments"][0]
            text = attachment["payload"]["url"]
        elif self._is_file_message(message):
            attachment = message["message"]["attachments"][0]
            text = attachment["payload"]["url"]
        else:
            logger.warning(
                "Received a message from instagram that we can not "
                f"handle. Message: {message}"
            )
            return

        await self._handle_user_message(text, self.get_user_id(), metadata)

    async def postback(
        self, message: Dict[Text, Any], metadata: Optional[Dict[Text, Any]]
    ) -> None:

        text = message["postback"]["payload"]
        await self._handle_user_message(text, self.get_user_id(), metadata)

    async def _handle_user_message(
        self, text: Text, sender_id: Text, metadata: Optional[Dict[Text, Any]]
    ) -> None:

        out_channel = MessengerBot(self.client)
        await out_channel.send_action(sender_id, sender_action="mark_seen")

        user_msg = UserMessage(
            text, out_channel, sender_id, input_channel=self.name(), metadata=metadata
        )
        await out_channel.send_action(sender_id, sender_action="typing_on")
        try:
            await self.on_new_message(user_msg)
        except Exception:
            logger.exception(
                "Exception when trying to handle webhook for instagram message."
            )
            pass
        finally:
            await out_channel.send_action(sender_id, sender_action="typing_off")


class MessengerBot(OutputChannel):

    @classmethod
    def name(cls) -> Text:
        return "instagram"

    def __init__(self, messenger_client: MessengerClient) -> None:

        self.messenger_client = messenger_client
        super().__init__()

    def send(self, recipient_id: Text, element: Any) -> None:
        self.messenger_client.send(element.to_dict(), recipient_id, "RESPONSE")

    async def send_text_message(
        self, recipient_id: Text, text: Text, **kwargs: Any
    ) -> None:
        for message_part in text.strip().split("\n\n"):
            self.send(recipient_id, FBText(text=message_part))

    async def send_image_url(
        self, recipient_id: Text, image: Text, **kwargs: Any
    ) -> None:

        self.send(recipient_id, Image(url=image))

    async def send_action(self, recipient_id: Text, sender_action: Text) -> None:
        self.messenger_client.send_action(
            SenderAction(sender_action).to_dict(), recipient_id
        )

    async def send_text_with_buttons(
        self,
        recipient_id: Text,
        text: Text,
        buttons: List[Dict[Text, Any]],
        **kwargs: Any,
    ) -> None:

        for button in buttons:
            button["type"] = "postback"
            print(button)

        print(f'Buttons text {buttons}')


        if len(buttons) > 3:
            rasa.shared.utils.io.raise_warning(
                "instagram API currently allows only up to 3 buttons. "
                "If you add more, all will be ignored."
            )
            await self.send_text_message(recipient_id, text, **kwargs)
        else:
            self._add_postback_info(buttons)

            payload = {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "generic",
                        "elements":[{
                            "title": text,
                            "buttons": buttons
                        }]
                    },
                }
            }
            self.messenger_client.send(payload, recipient_id, "RESPONSE")

    async def send_quick_replies(
        self,
        recipient_id: Text,
        text: Text,
        quick_replies: List[Dict[Text, Any]],
        **kwargs: Any,
    ) -> None:

        quick_replies = self._convert_to_quick_reply(quick_replies)
        self.send(recipient_id, FBText(text=text, quick_replies=quick_replies))

    async def send_elements(
        self, recipient_id: Text, elements: Iterable[Dict[Text, Any]], **kwargs: Any
    ) -> None:
        for element in elements:
            if "buttons" in element:
                self._add_postback_info(element["buttons"])
                print(f'for buttons {self._add_postback_info(element["buttons"])}')

        payload = {
            "attachment": {
                "type": "template",
                "payload": {"template_type": "generic", "elements": elements},
            }
        }
        self.messenger_client.send(payload, recipient_id, "RESPONSE")
        print(f'RESPONSE 2 {self.messenger_client.send(payload, recipient_id, "RESPONSE")}')

    async def send_custom_json(
        self, recipient_id: Text, json_message: Dict[Text, Any], **kwargs: Any
    ) -> None:
        recipient_id = json_message.pop("sender", {}).pop("id", None) or recipient_id

        self.messenger_client.send(json_message, recipient_id, "RESPONSE")

    @staticmethod
    def _add_postback_info(buttons: List[Dict[Text, Any]]) -> None:
        for button in buttons:
            if "type" not in button:
                button["type"] = "postback"

    @staticmethod
    def _convert_to_quick_reply(quick_replies: List[Dict[Text, Any]]) -> QuickReplies:

        insta_quick_replies = []
        for quick_reply in quick_replies:
            try:
                insta_quick_replies.append(
                    QuickReply(
                        title=quick_reply["title"],
                        payload=quick_reply["payload"],
                        content_type=quick_reply.get("content_type"),
                    )
                )
            except KeyError as e:
                raise ValueError(
                    'instagram quick replies must define a "{}" field.'.format(e.args[0])
                )

        return QuickReplies(quick_replies=insta_quick_replies)


class InstagramInput(InputChannel):
    @classmethod
    def name(cls) -> Text:
        return "instagram"

    @classmethod
    def from_credentials(cls, credentials: Optional[Dict[Text, Any]]) -> InputChannel:
        if not credentials:
            cls.raise_missing_credentials_exception()

        return cls(
            credentials.get("verify"),
            credentials.get("secret"),
            credentials.get("page-access-token"),
        )

    def __init__(self, insta_verify: Text, insta_secret: Text, insta_access_token: Text) -> None:
        self.insta_verify = insta_verify
        self.insta_secret = insta_secret
        self.insta_access_token = insta_access_token

    def blueprint(
        self, on_new_message: Callable[[UserMessage], Awaitable[Any]]
    ) -> Blueprint:
        insta_webhook = Blueprint("insta_webhook", __name__)
        @insta_webhook.route("/", methods=["GET"])
        async def health(request: Request) -> HTTPResponse:
            return response.json({"status": "ok"})

        @insta_webhook.route("/webhook", methods=["GET"])
        async def token_verification(request: Request) -> HTTPResponse:
            if request.args.get("hub.verify_token") == self.insta_verify:
                return response.text(request.args.get("hub.challenge"))
            else:
                logger.warning(
                    "Invalid insta verify token! Make sure this matches "
                    "your webhook settings on the instagram app."
                )
                return response.text("failure, invalid token")

        @insta_webhook.route("/webhook", methods=["POST"])
        async def webhook(request: Request) -> HTTPResponse:
            signature = request.headers.get("X-Hub-Signature") or ""
            if not self.validate_hub_signature(self.insta_secret, request.body, signature):
                logger.warning(
                    "Wrong secret! Make sure this matches the "
                    "secret in your instagram app settings"
                )
                return response.text("not validated")

            messenger = Messenger(self.insta_access_token, on_new_message)

            metadata = self.get_metadata(request)
            await messenger.handle(request.json, metadata)
            return response.text("success")

        return insta_webhook

    @staticmethod
    def validate_hub_signature(
        app_secret: Text, request_payload: bytes, hub_signature_header: Text
    ) -> bool:
        try:
            hash_method, hub_signature = hub_signature_header.split("=")
        except Exception:
            pass
        else:
            digest_module = getattr(hashlib, hash_method)
            hmac_object = hmac.new(
                bytearray(app_secret, "utf8"), request_payload, digest_module
            )
            generated_hash = hmac_object.hexdigest()
            if hub_signature == generated_hash:
                return True
        return False

    def get_output_channel(self) -> OutputChannel:
        client = MessengerClient(self.insta_access_token)
        return MessengerBot(client)