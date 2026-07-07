package com.bbdwz.emailservice.controller;

import com.bbdwz.emailservice.model.SendMailRequest;
import com.bbdwz.emailservice.provider.MailProvider;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/mail")
public class MailController {

    private final MailProvider mailProvider;

    public MailController(MailProvider mailProvider) {
        this.mailProvider = mailProvider;
    }

    @PostMapping("/send")
    public ResponseEntity<Map<String, String>> send(@Valid @RequestBody SendMailRequest request) {
        mailProvider.send(request);
        return ResponseEntity.ok(Map.of("status", "sent"));
    }
}
