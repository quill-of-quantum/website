package com.bbdwz.emailservice.model;

import java.time.Instant;
import java.util.List;

public record ReceivedMail(
        String from,
        List<String> to,
        String subject,
        String text,
        Instant receivedAt,
        String messageId
) {
}
