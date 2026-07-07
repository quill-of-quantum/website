package com.bbdwz.emailservice.provider;

import com.bbdwz.emailservice.model.ReceivedMail;
import com.bbdwz.emailservice.model.SendMailRequest;
import com.bbdwz.emailservice.receiver.ImapIdleListener;
import com.bbdwz.emailservice.sender.MailSenderService;
import org.springframework.stereotype.Component;

import java.util.function.Consumer;

@Component
public class ImapSmtpMailProvider implements MailProvider {

    private final MailSenderService senderService;
    private final ImapIdleListener idleListener;

    public ImapSmtpMailProvider(MailSenderService senderService, ImapIdleListener idleListener) {
        this.senderService = senderService;
        this.idleListener = idleListener;
    }

    @Override
    public void send(SendMailRequest request) {
        senderService.send(request);
    }

    @Override
    public void startReceiving(Consumer<ReceivedMail> mailConsumer) {
        idleListener.start(mailConsumer);
    }

    @Override
    public void stopReceiving() {
        idleListener.stop();
    }
}
