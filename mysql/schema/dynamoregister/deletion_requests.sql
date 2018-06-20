CREATE TABLE `deletion_requests` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `user_id` int(10) unsigned NOT NULL,
  `request_time` datetime NOT NULL,
  `status` enum('new','activated') NOT NULL DEFAULT 'new',
  PRIMARY KEY (`id`),
  KEY `user` (`user_id`),
  KEY `timestamp` (`timestamp`),
  KEY `status` (`status`)
) ENGINE=MyISAM DEFAULT CHARSET=latin1;
