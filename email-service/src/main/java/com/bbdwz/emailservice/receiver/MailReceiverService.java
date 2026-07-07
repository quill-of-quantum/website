package com.bbdwz.emailservice.receiver;

import com.bbdwz.emailservice.event.MailReceivedEvent;
import com.bbdwz.emailservice.provider.MailProvider;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

@Service
@ConditionalOnProperty(prefix = "mail.receiver", name = "enabled", havingValue = "true", matchIfMissing = true)
public class MailReceiverService {

    private final MailProvider mailProvider;
    private final ApplicationEventPublisher eventPublisher;

    public MailReceiverService(MailProvider mailProvider, ApplicationEventPublisher eventPublisher) {
        this.mailProvider = mailProvider;
        this.eventPublisher = eventPublisher;
    }

    @PostConstruct
    public void start() {
        mailProvider.startReceiving(mail -> eventPublisher.publishEvent(new MailReceivedEvent(this, mail)));
    }

    @PreDestroy
    public void stop() {
        mailProvider.stopReceiving();
    }
}
