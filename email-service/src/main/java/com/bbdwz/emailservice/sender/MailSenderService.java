package com.bbdwz.emailservice.sender;

import com.bbdwz.emailservice.config.MailAccountProperties;
import com.bbdwz.emailservice.model.SendMailRequest;
import com.bbdwz.emailservice.provider.auth.MailAuthStrategy;
import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.Session;
import jakarta.mail.Transport;
import jakarta.mail.internet.InternetAddress;
import jakarta.mail.internet.MimeMessage;
import jakarta.mail.internet.MimeBodyPart;
import jakarta.mail.internet.MimeMultipart;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.util.Properties;

@Service
public class MailSenderService {

    private final MailAccountProperties properties;
    private final MailAuthStrategy authStrategy;

    public MailSenderService(MailAccountProperties properties, MailAuthStrategy authStrategy) {
        this.properties = properties;
        this.authStrategy = authStrategy;
    }

    public void send(SendMailRequest request) {
        try {
            Session session = Session.getInstance(smtpProperties(), authStrategy.authenticator());
            MimeMessage message = new MimeMessage(session);
            message.setFrom(new InternetAddress(properties.getAccount().getAddress()));
            message.setRecipients(Message.RecipientType.TO, InternetAddress.parse(request.getTo()));
            message.setSubject(request.getSubject(), StandardCharsets.UTF_8.name());
            if (request.getHtml() != null && !request.getHtml().isBlank()) {
                MimeBodyPart plainPart = new MimeBodyPart();
                plainPart.setText(request.getText(), StandardCharsets.UTF_8.name());
                MimeBodyPart htmlPart = new MimeBodyPart();
                htmlPart.setContent(request.getHtml(), "text/html; charset=UTF-8");
                MimeMultipart alternatives = new MimeMultipart("alternative");
                alternatives.addBodyPart(plainPart);
                alternatives.addBodyPart(htmlPart);
                message.setContent(alternatives);
            } else {
                message.setText(request.getText(), StandardCharsets.UTF_8.name());
            }
            Transport.send(message);
        } catch (MessagingException e) {
            throw new IllegalStateException("Failed to send mail", e);
        }
    }

    private Properties smtpProperties() {
        Properties target = new Properties();
        target.put("mail.smtp.host", properties.getSmtp().getHost());
        target.put("mail.smtp.port", String.valueOf(properties.getSmtp().getPort()));
        target.put("mail.smtp.ssl.enable", String.valueOf(properties.getSmtp().isSsl()));
        target.put("mail.smtp.starttls.enable", String.valueOf(properties.getSmtp().isStarttls()));
        target.put("mail.smtp.auth.mechanisms", properties.getSmtp().getAuthMechanisms());
        target.put("mail.smtp.connectiontimeout", "10000");
        target.put("mail.smtp.timeout", "20000");
        target.put("mail.smtp.writetimeout", "20000");
        authStrategy.applySmtp(target);
        return target;
    }
}
