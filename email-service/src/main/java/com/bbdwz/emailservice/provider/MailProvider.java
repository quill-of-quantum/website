package com.bbdwz.emailservice.provider;

import com.bbdwz.emailservice.model.ReceivedMail;
import com.bbdwz.emailservice.model.SendMailRequest;

import java.util.function.Consumer;

public interface MailProvider {

    void send(SendMailRequest request);

    void startReceiving(Consumer<ReceivedMail> mailConsumer);

    void stopReceiving();
}
