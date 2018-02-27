<?php

include_once(__DIR__ . '/../dynamo/common/db_conf.php');
include_once(__DIR__ . '/common.php');

// General note:
// MySQL DATETIME accepts and returns local time. Always use UNIX_TIMESTAMP and FROM_UNIXTIME to interact with the DB.

class Requests {
  public $format = 'json';
  public $return_data = true;

  private $_db = NULL;
  private $_uid = 0;
  private $_uname = '';
  private $_read_only = true;

  public function __construct($cert_dn, $issuer_dn, $service, $as_user = NULL)
  {
    global $db_conf;

    $this->_db = new mysqli($db_conf['host'], $db_conf['user'], $db_conf['password'], 'dynamoregister');

    $sid = 0;
    $authorized = get_user($this->_db, $cert_dn, $issuer_dn, $service, $as_user, $this->_uid, $this->_uname, $sid);

    if ($this->_uid == 0 || $sid == 0)
      $this->send_response(400, 'BadRequest', 'Unknown user');

    $this->_read_only = !$authorized;
  }

  public function execute($command, $request)
  {
    if (!in_array($command, array('copy', 'delete', 'pollcopy', 'polldeletion', 'cancelcopy', 'canceldeletion')))
      $this->send_response(400, 'BadRequest', 'Invalid command (possible values: copy, delete, list)');

    if ($command != 'pollcopy' && $command != 'polldeletion' && $this->_read_only)
      $this->send_response(400, 'BadRequest', 'User not authorized');

    $this->sanitize_request($command, $request);

    if ($command == 'copy') {
      $this->exec_copy($request);
    }
    else if ($command == 'delete') {
      $this->exec_delete($request);
    }
    else if ($command == 'pollcopy') {
      $this->exec_poll($request, 'copy');
    }
    else if ($command == 'polldeletion') {
      $this->exec_poll($request, 'deletion');
    }
    else if ($command == 'cancelcopy') {
      $this->exec_cancel($request, 'copy');
    }
    else if ($command == 'canceldeletion') {
      $this->exec_cancel($request, 'deletion');
    }
  }

  private function exec_copy($request)
  {
    if (isset($_SERVER['CONTENT_TYPE']) && strcasecmp(trim($_SERVER['CONTENT_TYPE']), 'application/json') == 0) {
      // json uploaded
      $input = file_get_contents('php://input');

      $request = json_decode($input, true);
      if (!is_array($request))
        $this->send_response(400, 'BadRequest', 'Invalid data posted');
    }

    $request_id = isset($request['request_id']) ? $request['request_id'] : NULL;

    if (isset($request['item'])) {
      if (is_string($request['item']))
        $item = explode(',', trim($request['item'], ','));
      else
        $item = $request['item'];
    }
    else if ($request_id === NULL)
      $this->send_response(400, 'BadRequest', 'Item not given');
    else
      $item = NULL;

    if (isset($request['site']))
      $site = $request['site'];
    else if ($request_id === NULL)
      $this->send_response(400, 'BadRequest', 'Site not given');
    else
      $site = NULL;

    // we need the table to be consistent between get_data and insert
    $this->lock_table('copy');

    $existing_data = $this->get_copy_data($request_id, $item, $site);

    if ($request_id !== NULL && count($existing_data) == 0)
      $this->send_response(400, 'BadRequest', 'Request not found');
    
    if (count($existing_data) == 0) {
      // this is a new copy

      $ncopy = isset($request['n']) ? $request['n'] : 1;
      if (strpos($site, '*') === false) // non-wildcard
        $ncopy = 1;

      $request_id = $this->create_copy_request($item, $site, 'AnalysisOps', $ncopy);

      if ($request_id != 0) {
        $new_copy = $this->get_copy_data($request_id);
        $this->send_response(200, 'OK', 'Copy requested', array_values($new_copy));
      }
      else
        $this->send_response(500, 'InternalError', 'Failed to request copy');
    }
    else {
      // known copy - update
      $updates = array();
      /* if (isset($request['group'])) */
      /*   $updates['group'] = $request['group']; */
      if (isset($request['n']))
        $updates['n'] = $request['n'];

      $updated_data = array();
      $request_ids = array_keys($existing_data);

      foreach ($request_ids as $request_id) {
        if ($existing_data[$request_id]['status'] != 'new')
          $updates['status'] = 'updated';
        else
          $updates['status'] = 'new';

        $updated_data[] = $this->update_copy_request($request_id, $updates);
      }

      $this->send_response(200, 'OK', 'Request updated', $updated_data);
    }
  }

  private function exec_delete($request)
  {
    if (isset($_SERVER['CONTENT_TYPE']) && strcasecmp(trim($_SERVER['CONTENT_TYPE']), 'application/json') == 0) {
      // json uploaded
      $input = file_get_contents('php://input');

      $request = json_decode($input, true);
      if (!is_array($request))
        $this->send_response(400, 'BadRequest', 'Invalid data posted');
    }

    $request_id = isset($request['request_id']) ? $request['request_id'] : NULL;

    if (isset($request['item'])) {
      if (is_string($request['item']))
        $item = explode(',', trim($request['item'], ','));
      else
        $item = $request['item'];
    }
    else if ($request_id === NULL)
      $this->send_response(400, 'BadRequest', 'Item not given');
    else
      $item = NULL;

    if (isset($request['site']))
      $site = $request['site'];
    else if ($request_id === NULL)
      $this->send_response(400, 'BadRequest', 'Site not given');
    else
      $site = NULL;

    // we need the table to be consistent between get_deletion_data and insert
    $this->lock_table('deletion');

    $existing_data = $this->get_deletion_data($request_id, $item, $site);

    if ($request_id !== NULL && count($existing_data) == 0)
      $this->send_response(400, 'BadRequest', 'Request not found');
    
    if (count($existing_data) == 0) {
      // this is a new deletion

      $request_id = $this->create_deletion_request($item, $site);

      if ($request_id != 0) {
        $new_deletion = $this->get_deletion_data($request_id);
        $this->send_response(200, 'OK', 'Deletion requested', array_values($new_deletion));
      }
      else
        $this->send_response(400, 'InternalError', 'Failed to request deletion');
    }
    else
      $this->send_response(200, 'OK', 'Request exists', array_values($existing_data));
  }

  private function exec_poll($request, $type)
  {
    $request_id = isset($request['request_id']) ? $request['request_id'] : NULL;

    if (isset($request['item']))
      $item = $request['item']; // can be an array
    else
      $item = NULL;

    if (isset($request['site']))
      $site = $request['site'];
    else
      $site = NULL;

    if ($request_id === NULL && $item == NULL && $site == NULL)
      $this->send_response(400, 'BadRequest', 'Need request_id, item, or site');

    if ($type == 'copy')
      $existing_data = $this->get_copy_data($request_id, $item, $site);
    else
      $existing_data = $this->get_deletion_data($request_id, $item, $site);

    if (count($existing_data) == 0)
      $this->send_response(200, 'EmptyResult', 'Request not found');
    else
      $this->send_response(200, 'OK', 'Request found', array_values($existing_data));
  }

  private function exec_cancel($request, $type)
  {
    if (!isset($request['request_id']))
      $this->send_response(400, 'BadRequest', 'No request id given');

    $request_id = $request['request_id'];

    // we need the table to be consistent between get_deletion_data and insert
    $this->lock_table($type);

    if ($type == 'copy')
      $existing_data = $this->get_copy_data($request_id);
    else if ($type == 'deletion')
      $existing_data = $this->get_deletion_data($request_id);

    if (count($existing_data) == 0)
      $this->send_response(400, 'BadRequest', 'Invalid request id');

    $current_status = $existing_data[$request_id]['status'];
    if ($current_status == 'cancelled')
      $this->send_response(200, 'OK', 'Request already cancelled', $existing_data[$request_id]);
    else if ($current_status == 'completed' || $current_status == 'rejected')
      $this->send_response(400, 'BadRequest', 'Cannot cancel completed or rejected requests', $existing_data[$request_id]);

    if ($type == 'copy')
      $updated_request = $this->update_copy_request($request_id, array('status' => 'cancelled'));
    else if ($type == 'deletion')
      $updated_request = $this->cancel_deletion_request($request_id);

    $this->send_response(200, 'OK', 'Request cancelled', $updated_request);
  }

  private function sanitize_request($command, &$request)
  {
    $allowed_fields = array('request_id', 'item', 'site');

    if ($command == 'copy')
      $allowed_fields = array_merge($allowed_fields, array('group', 'n'));

    foreach (array_keys($request) as $key) {
      if (in_array($key, $allowed_fields)) {
        if ($key == 'request_id' || $key == 'n')
          $request[$key] = 0 + $request[$key];
        else
          $request[$key] = $this->_db->real_escape_string($request[$key]);
      }
      else
        $this->send_response(400, 'BadRequest', 'Field "' . $key . '" not allowed for operation "' . $command . '".');
    }
  }

  private function get_copy_data($request_id, $item = NULL, $site = NULL, $all_users = false)
  {
    // structure:
    // array(
    //   id => array('request_id' => id, 'item' => array(name,), ...),
    //   ...
    // )

    $data = array();

    $fields = array(
      'r.`id`',
      'r.`site`',
      'r.`group`',
      'r.`num_copies`',
      'r.`status`',
      'r.`first_request_time`',
      'r.`last_request_time`',
      'r.`request_count`',
      'r.`rejection_reason`',
      'a.`site`',
      'a.`status`',
      'a.`updated`'
    );
    if ($all_users)
      $fields[] = 'u.`name`';

    $query = 'SELECT ' . implode(',', $fields) . ' FROM `copy_requests` AS r';
    $query .= ' LEFT JOIN `active_copies` AS a ON a.`request_id` = r.`id`';
    if ($all_users)
      $query .= ' INNER JOIN `users` AS u ON u.`id` = r.`user_id`';

    $where_clause = array();
    $params = array('');

    if (!$all_users) {
      $where_clause[] = 'r.`user_id` = ?';
      $params[0] .= 'i';
      $params[] = &$this->_uid;
    }

    if ($request_id !== NULL) {
      $where_clause[] = 'r.`id` = ?';
      $params[0] .= 'i';
      $params[] = &$request_id;
    }
    else {
      // limit to live requests
      $where_clause[] = 'r.`status` IN (\'new\', \'activated\', \'updated\')';

      if ($site !== NULL) {
        $where_clause[] =  'r.`site` = ?';
        $params[0] .= 's';
        $params[] = &$site;
      }

      if ($item !== NULL) {
        $request_ids = $this->get_ids_by_items('copy', $item, $params, $where_clause);
        if (count($request_ids) == 0)
          return $data;
        else
          $where_clause[] = 'r.`id` IN (' . implode(',', $request_ids) . ')';
      }
    }

    if (count($where_clause) != 0)
      $query .= ' WHERE ' . implode(' AND ', $where_clause);

    $query .= ' ORDER BY r.`id`';

    $stmt = $this->_db->prepare($query);

    if (count($params) > 1)
      call_user_func_array(array($stmt, "bind_param"), $params);

    if ($all_users)
      $stmt->bind_result($rid, $site, $group, $ncopy, $status, $first_request, $last_request, $request_count, $reason, $asite, $astatus, $atimestamp, $uname);
    else
      $stmt->bind_result($rid, $site, $group, $ncopy, $status, $first_request, $last_request, $request_count, $reason, $asite, $astatus, $atimestamp);

    $stmt->execute();

    $_rid = 0;
    while ($stmt->fetch()) {
      if ($rid != $_rid) {
        $_rid = $rid;

        $data[$rid] = array(
          'request_id' => $rid,
          'site' => $site,
          'item' => array(),
          'group' => $group,
          'n' => $ncopy,
          'status' => $status,
          'first_request' => $first_request,
          'last_request' => $last_request,
          'request_count' => $request_count
        );

        $datum = &$data[$rid];

        if ($all_users)
          $datum['user'] = $uname;

        if ($status == 'rejected')
          $datum['reason'] = $reason;
        else if ($status == 'activated' || $status == 'completed')
          $datum['copy'] = array();
      }

      if ($astatus !== NULL)
        $datum['copy'][] = array('site' => $asite, 'status' => $astatus, 'updated' => $atimestamp);
    }

    $stmt->close();

    if (count($data) == 0)
      return $data;

    // now get the items for these requests
    $query = 'SELECT i.`request_id`, i.`item` FROM `copy_request_items` AS i WHERE i.`request_id` IN (';
    $query .= implode(',', array_keys($data));
    $query .= ')';

    $stmt = $this->_db->prepare($query);
    $stmt->bind_result($rid, $item);
    $stmt->execute();

    while ($stmt->fetch())
      $data[$rid]['item'][] = $item;

    $stmt->close();

    return $data;
  }

  private function create_copy_request($item, $site, $group, $ncopy)
  {
    $query = 'INSERT INTO `copy_requests` (`site`, `group`, `num_copies`, `user_id`, `first_request_time`, `last_request_time`) VALUES (?, ?, ?, ?, NOW(), NOW())';
    $stmt = $this->_db->prepare($query);
    $stmt->bind_param('ssii', $site, $group, $ncopy, $this->_uid);
    $stmt->execute();
    $request_id = $stmt->insert_id;
    $stmt->close();

    $query = sprintf('INSERT INTO `copy_request_items` (`request_id`, `item`) VALUES (%d, ?)', $request_id);
    $stmt = $this->_db->prepare($query);
    $stmt->bind_param('s', $item_name);
    if (is_array($item)) {
      foreach ($item as $nm) {
        $item_name = $nm;
        $stmt->execute();
      }
    }
    else {
      $item_name = $item;
      $stmt->execute();
    }
    $stmt->close();

    return $request_id;
  }

  private function update_copy_request($request_id, $content)
  {
    $query = 'UPDATE `copy_requests` SET `last_request_time` = NOW(), ';

    $params = array('');

    if (isset($content['group'])) {
      $set[] = '`group` = ?';
      $params[0] .= 's';
      $params[] = &$content['group'];
    }

    if (isset($content['n'])) {
      $set[] = '`num_copies` = IF(`site` LIKE \'%*%\', ?, 1)';
      $params[0] .= 'i';
      $params[] = &$content['n'];
    }

    $increment = true;

    if (isset($content['status'])) {
      $set[] = '`status` = ?';
      $params[0] .= 's';
      $params[] = &$content['status'];

      if ($content['status'] == 'cancelled')
        $increment = false;
    }

    if ($increment)
      $query .= '`request_count` = `request_count` + 1, ';

    $query .= implode(', ', $set);

    $query .= sprintf(' WHERE `id` = %d', $request_id);

    $stmt = $this->_db->prepare($query);
    call_user_func_array(array($stmt, "bind_param"), $params);
    $stmt->execute();
    $stmt->close();

    $data = $this->get_copy_data($request_id);

    return $data[$request_id];
  }

  private function get_deletion_data($request_id, $item = NULL, $site = NULL, $all_users = false)
  {
    // structure:
    // array(
    //   id => array('request_id' => id, 'item' => name, ...),
    //   ...
    // )

    $data = array();

    $fields = array(
      'r.`id`',
      'r.`site`',
      'r.`status`',
      'r.`timestamp`',
      'r.`rejection_reason`',
      'a.`status`',
      'a.`updated`'
    );
    if ($all_users)
      $fields[] = 'u.`name`';
    
    $query = 'SELECT ' . implode(',', $fields) . ' FROM `deletion_requests` AS r';
    $query .= ' LEFT JOIN `active_deletions` AS a ON a.`request_id` = r.`id`';
    if ($all_users)
      $query .= ' INNER JOIN `users` AS u ON u.`id` = r.`user_id`';

    $where_clause = array();
    $params = array('');

    if (!$all_users) {
      $where_clause[] = 'r.`user_id` = ?';
      $params[0] .= 'i';
      $params[] = &$this->_uid;
    }

    if ($request_id !== NULL) {
      $where_clause[] = 'r.`id` = ?';
      $params[0] .= 'i';
      $params[] = &$request_id;
    }
    else {
      // limit to live requests
      $where_clause[] = 'r.`status` IN (\'new\', \'activated\')';

      if ($site !== NULL) {
        $where_clause[] =  'r.`site` = ?';
        $params[0] .= 's';
        $params[] = &$site;
      }

      if ($item !== NULL) {
        $request_ids = $this->get_ids_by_items('deletion', $item, $params, $where_clause);
        if (count($request_ids) == 0)
          return $data;
        else
          $where_clause[] = 'r.`id` IN (' . implode(',', $request_ids) . ')';
      }
    }

    if (count($where_clause) != 0)
      $query .= ' WHERE ' . implode(' AND ', $where_clause);

    $query .= ' ORDER BY r.`id`';

    $stmt = $this->_db->prepare($query);

    if (count($params) > 1)
      call_user_func_array(array($stmt, "bind_param"), $params);

    if ($all_users)
      $stmt->bind_result($rid, $site, $status, $timestamp, $reason, $astatus, $atimestamp, $uname);
    else
      $stmt->bind_result($rid, $site, $status, $timestamp, $reason, $astatus, $atimestamp);

    $stmt->execute();

    $_rid = 0;
    while ($stmt->fetch()) {
      if ($rid != $_rid) {
        $_rid = $rid;

        $data[$rid] = array(
          'request_id' => $rid,
          'site' => $site,
          'item' => array(),
          'status' => $status,
          'requested' => $timestamp
        );

        $datum = &$data[$rid];

        if ($all_users)
          $datum['user'] = $uname;

        if ($status == 'rejected')
          $datum['reason'] = $reason;
        else if ($status == 'activated' || $status == 'completed')
          $datum['deletion'] = array();
      }

      if ($astatus !== NULL)
        $datum['deletion'][] = array('status' => $astatus, 'updated' => $atimestamp);
    }

    $stmt->close();

    // now get the items for these requests
    $query = 'SELECT i.`request_id`, i.`item` FROM `deletion_request_items` WHERE i.`request_id` IN (';
    $query .= implode(',', array_keys($data));
    $query .= ')';

    $stmt = $this->_db->prepare($query);
    $stmt->bind_result($rid, $item);
    $stmt->execute();

    while ($stmt->fetch())
      $data[$rid]['item'][] = $item;

    $stmt->close();

    return $data;
  }

  private function create_deletion_request($item, $site)
  {
    $query = 'INSERT INTO `deletion_requests` (`site`, `user_id`, `timestamp`) VALUES (?, ?, NOW())';
    $stmt = $this->_db->prepare($query);
    $stmt->bind_param('si', $site, $this->_uid);
    $stmt->execute();
    $request_id = $stmt->insert_id;
    $stmt->close();

    $query = sprintf('INSERT INTO `deletion_request_items` (`request_id`, `item`) VALUES (%d, ?)', $request_id);
    $stmt = $this->_db->prepare($query);
    $stmt->bind_param('s', $item_name);
    if (is_array($item)) {
      foreach ($item as $nm) {
        $item_name = $nm;
        $stmt->execute();
      }
    }
    else {
      $item_name = $item;
      $stmt->execute();
    }
    $stmt->close();

    return $request_id;
  }

  private function cancel_deletion_request($request_id)
  {
    $query = sprintf('UPDATE `deletion_requests` AS r SET `status` = \'cancelled\' WHERE r.`id` = %d', $request_id);
    $this->_db->query($query);

    $data = $this->get_deletion_data($request_id);

    return $data[$request_id];
  }

  private function get_ids_by_items($table_type, $item, $params, $where_clause)
  {
    // we'll fetch the id of the request matching all other constraints + item

    // (id, item) from request items where id is in the list of requests that satisfy other constraints
    $query = sprintf('SELECT i.`request_id`, i.`item` FROM `%s_request_items` AS i', $table_type);

    if (is_array($item)) {
      // write the list to a temporary table first
      $this->_db->query(sprintf('CREATE TEMPORARY TABLE `%s_items_tmp` (`item` varchar(512) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL)', $table_type));
      $stmt = $this->_db->prepare(sprintf('INSERT INTO `%s_items_tmp` VALUES (?)', $table_type));
      $stmt->bind_param('s', $item_name);
      foreach ($item as $nm) {
        $item_name = $nm;
        $stmt->execute();
      }
      $stmt->close();

      $query .= sprintf(' RIGHT JOIN `%s_items_tmp` as t ON t.`item` = i.`item`', $table_type);
    }

    $query .= sprintf(' WHERE i.`request_id` IN (SELECT r.`id` FROM `%s_requests` AS r', $table_type);

    // where on the r subquery
    if (count($where_clause) != 0)
      $query .= ' WHERE ' . implode(' AND ', $where_clause);

    $query .= ')';

    if (!is_array($item)) {
      $query .= ' AND i.`item` = ?';
      $params[0] .= 's';
      $params[] = &$item;
    }

    $query .= ' ORDER BY i.`request_id`';

    $stmt = $this->_db->prepare($query);

    if (count($params) > 1)
      call_user_func_array(array($stmt, "bind_param"), $params);

    $stmt->bind_result($rid, $item);
    $stmt->execute();

    // loop through fetched items
    // if all items under a request id is non-NULL, we have a matching id
    $request_ids = array();
    $_rid = 0;
    $all_matched = false;
    while ($stmt->fetch()) {
      if ($rid != $_rid) {
        if ($all_matched)
          $request_ids[] = $_rid;

        $_rid = $rid;
        $all_matched = true;
      }
      
      if ($item === NULL)
        $all_matched = false;
    }
    if ($all_matched)
      $request_ids[] = $_rid;

    $stmt->close();

    return $request_ids;
  }

  private function lock_table($operation = '')
  {
    if ($operation == 'copy')
      $query = 'LOCK TABLES `copy_requests` WRITE, `copy_requests` AS r WRITE, `active_copies` AS a WRITE, `copy_request_items` WRITE, `copy_request_items` AS i WRITE';
    else if ($operation == 'deletion')
      $query = 'LOCK TABLES `deletion_requests` WRITE, `deletion_requests` AS r WRITE, `active_deletions` AS a WRITE, `deletion_request_items` WRITE, `deletion_request_items` AS i WRITE';
    else
       $query = 'UNLOCK TABLES';

    $this->_db->query($query);
  }

  private function send_response($code, $result, $message, $data = NULL)
  {
    // Table lock will be released at the end of the session. Explicit unlocking is in principle unnecessary.
    $this->lock_table(false);
    send_response($code, $result, $message, $this->return_data ? $data : NULL, $this->format);
  }
}

?>
