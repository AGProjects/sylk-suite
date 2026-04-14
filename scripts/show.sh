#!/bin/bash
echo
echo "Running components"
echo "=================="
echo

sudo ps ax -o cmd=|grep -E "sips|media|relay|xcap|docker"|cut -f 1-200 -d "/"|grep -v -E  "grep|\hooks"|awk '{$1=$1; print}'|sort |uniq

echo
echo "OpenSIPS domain"
echo "==============="
echo
sudo opensips-cli -x mi domain_dump
echo

