package com.bbdwz.emailservice.event;

import com.bbdwz.emailservice.model.ReceivedMail;
import org.springframework.context.ApplicationEvent;

public class MailReceivedEvent extends ApplicationEvent {

    private final ReceivedMail mail;

    public MailReceivedEvent(Object source, ReceivedMail mail) {
        super(source);
        this.mail = mail;
    }

    public ReceivedMail getMail() {
        return mail;
    }
}
